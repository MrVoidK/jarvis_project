import os
import sys
import glob

# --- Windows CUDA DLL Fix Başlangıcı ---
# Eğer işletim sistemi Windows ise, venv içindeki nvidia pip paketlerinin DLL'lerini Python'a tanıtıyoruz.
if os.name == "nt":
    site_packages = os.path.join(sys.prefix, "Lib", "site-packages")
    nvidia_bins = glob.glob(os.path.join(site_packages, "nvidia", "*", "bin"))
    for bin_dir in nvidia_bins:
        try:
            os.add_dll_directory(bin_dir)  # Python'un DLL'leri görmesi için
        except Exception:
            pass
        # ctranslate2'nin alt süreçleri için ortam değişkenine de ekliyoruz:
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
# --- Windows CUDA DLL Fix Sonu ---
import logging
from collections import deque
from typing import Iterator, Optional

import numpy as np
import sounddevice as sd
import webrtcvad
from faster_whisper import WhisperModel

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("jarvis.ears")

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480 samples @ 16kHz/30ms
SILENCE_HANGOVER_MS = 700
MAX_WAIT_MS = 10000  # how long to wait for speech to start before giving up
MAX_UTTERANCE_MS = 20000  # how long a triggered utterance may run before force-stopping
PREROLL_FRAMES = 3  # ~90ms of audio kept before trigger, so onset isn't clipped
VAD_AGGRESSIVENESS = 2  # 0 (permissive) - 3 (aggressive filtering of non-speech)


def _load_model_with_fallback(model_size: str = "turbo") -> tuple[WhisperModel, str]:
    """Loads faster-whisper on CUDA/float16, falling back to CPU/int8 if unavailable.

    ctranslate2 can construct a CUDA model object successfully even when the
    CUDA/cuBLAS runtime is actually broken (e.g. missing DLLs) - the failure
    only surfaces on the first real inference call. A silent warm-up
    transcription forces that failure here instead of on the user's first
    utterance.
    """
    warmup_audio = np.zeros(SAMPLE_RATE, dtype=np.float32)  # 1s of silence
    try:
        model = WhisperModel(model_size, device="cuda", compute_type="float16")
        list(model.transcribe(warmup_audio)[0])
        return model, "cuda"
    except Exception as exc:
        logger.warning("CUDA kullanilamiyor (%s), CPU'ya dusuluyor.", exc)
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        return model, "cpu"


logger.info("Initializing Jarvis systems (Ears online)...")
model, _device = _load_model_with_fallback()
logger.info("faster-whisper '%s' cihazinda yuklendi.", _device)


def _vad_record() -> Optional[np.ndarray]:
    """Records one utterance using VAD endpointing; returns float32 mono audio or None on timeout/error."""
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    hangover_frames = SILENCE_HANGOVER_MS // FRAME_MS
    max_wait_frames = MAX_WAIT_MS // FRAME_MS
    max_speech_frames = MAX_UTTERANCE_MS // FRAME_MS

    preroll: deque[np.ndarray] = deque(maxlen=PREROLL_FRAMES)
    speech_frames: list[np.ndarray] = []
    triggered = False
    silence_run = 0
    wait_frames = 0
    speech_frame_count = 0

    logger.info("Dinleniyor (konusmaya baslayabilirsiniz)...")

    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME_SAMPLES
        ) as stream:
            while True:
                if not triggered and wait_frames >= max_wait_frames:
                    break
                if triggered and speech_frame_count >= max_speech_frames:
                    break

                frame, _overflowed = stream.read(FRAME_SAMPLES)
                frame = frame.reshape(-1)
                is_speech = vad.is_speech(frame.tobytes(), SAMPLE_RATE)

                if is_speech:
                    if not triggered:
                        # Prepend the frames right before the trigger so the
                        # first syllable (often soft/unvoiced) isn't clipped.
                        speech_frames.extend(preroll)
                        speech_frame_count += len(preroll)
                    triggered = True
                    silence_run = 0
                    speech_frames.append(frame)
                    speech_frame_count += 1
                elif triggered:
                    silence_run += 1
                    speech_frames.append(frame)
                    speech_frame_count += 1
                    if silence_run >= hangover_frames:
                        break
                else:
                    wait_frames += 1
                    preroll.append(frame)
    except sd.PortAudioError as exc:
        logger.error("Mikrofon acilamadi: %s", exc)
        return None

    if not speech_frames:
        logger.info("Konusma algilanmadi (timeout).")
        return None

    logger.info("Kayit tamamlandi, transkribe ediliyor...")
    audio_int16 = np.concatenate(speech_frames)
    return audio_int16.astype(np.float32) / 32768.0


def transcribe_once() -> Optional[str]:
    """Records a single VAD-bounded utterance and returns its transcript, or None if nothing was heard."""
    audio = _vad_record()
    if audio is None:
        return None

    try:
        segments, _info = model.transcribe(
            audio,
            beam_size=5,
            language="tr",
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )
        text = "".join(segment.text for segment in segments)
    except Exception as exc:
        # A single bad turn (e.g. a transient CUDA/ctranslate2 error) must not
        # kill listen_loop()'s otherwise-infinite generator.
        logger.error("Transkripsiyon basarisiz, bu turn atlaniyor: %s", exc)
        return None

    return text.strip()


def listen_loop() -> Iterator[str]:
    """Continuously yields transcripts, one per detected utterance. Skips silent/empty turns."""
    while True:
        text = transcribe_once()
        if text:
            yield text


if __name__ == "__main__":
    result = transcribe_once()
    logger.info("Jarvis Heard: %s", result)
