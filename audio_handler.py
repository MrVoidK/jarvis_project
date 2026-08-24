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
import time
from collections import deque
from enum import Enum, auto
from typing import Iterator, Optional

import numpy as np
import sounddevice as sd
import webrtcvad
from faster_whisper import WhisperModel
from openwakeword.model import Model as WakeWordModel
from openwakeword.utils import download_models as download_wakeword_models

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

WAKEWORD_MODEL_NAME = "hey_jarvis"
WAKEWORD_THRESHOLD = 0.5
WAKEWORD_CHUNK_MS = 80  # openWakeWord's native frame size; other sizes work but add latency
WAKEWORD_CHUNK_SAMPLES = SAMPLE_RATE * WAKEWORD_CHUNK_MS // 1000  # 1280 samples


class ListenState(Enum):
    IDLE = auto()  # waiting for the wake word, nothing is transcribed
    ACTIVE = auto()  # wake word triggered, VAD-recording an utterance


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


def _load_wakeword_model() -> WakeWordModel:
    """Loads the openWakeWord 'hey_jarvis' model, downloading its ONNX weights on first run.

    download_models() is idempotent (it skips files that already exist), so
    calling it on every startup costs a handful of os.path.exists() checks
    after the first run - no repeated network calls.
    """
    download_wakeword_models([f"{WAKEWORD_MODEL_NAME}_v0.1"])
    return WakeWordModel(wakeword_models=[WAKEWORD_MODEL_NAME], inference_framework="onnx")


logger.info("Initializing Jarvis systems (Ears online)...")
model, _device = _load_model_with_fallback()
logger.info("faster-whisper '%s' cihazinda yuklendi.", _device)
wakeword_model = _load_wakeword_model()
logger.info("openWakeWord '%s' modeli yuklendi.", WAKEWORD_MODEL_NAME)


def _vad_record(
    stream: sd.InputStream, trailing: Optional[np.ndarray] = None
) -> Optional[np.ndarray]:
    """Records one utterance using VAD endpointing on an already-open stream.

    Returns float32 mono audio, or None if nothing was said before the wait
    timeout. Takes an open stream (rather than opening its own) so the same
    microphone connection can be shared with wake-word listening - opening/
    closing the device on every state transition would add latency and risk
    dropping the first bit of audio.

    `trailing`, if given, is audio captured right after the wake word fired
    (see `_wait_for_wakeword`) that hasn't been classified by VAD yet. It's
    seeded into the pre-roll buffer so a command spoken with no pause after
    "Hey Jarvis" doesn't lose its first ~80ms.
    """
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    hangover_frames = SILENCE_HANGOVER_MS // FRAME_MS
    max_wait_frames = MAX_WAIT_MS // FRAME_MS
    max_speech_frames = MAX_UTTERANCE_MS // FRAME_MS

    preroll: deque[np.ndarray] = deque(maxlen=PREROLL_FRAMES)
    if trailing is not None:
        preroll.append(trailing)
    speech_frames: list[np.ndarray] = []
    triggered = False
    silence_run = 0
    wait_frames = 0
    speech_frame_count = 0

    logger.info("Dinleniyor (konusmaya baslayabilirsiniz)...")

    while True:
        if not triggered and wait_frames >= max_wait_frames:
            break
        if triggered and speech_frame_count >= max_speech_frames:
            break

        frame, overflowed = stream.read(FRAME_SAMPLES)
        if overflowed:
            logger.warning("Giris tamponu tasti (overflow) - ses kaybi olabilir.")
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

    if not speech_frames:
        logger.info("Konusma algilanmadi (timeout).")
        return None

    logger.info("Kayit tamamlandi, transkribe ediliyor...")
    audio_int16 = np.concatenate(speech_frames)
    return audio_int16.astype(np.float32) / 32768.0


def _wait_for_wakeword(stream: sd.InputStream) -> np.ndarray:
    """Blocks on an open stream until the wake word is detected, logging latency.

    Returns the triggering chunk itself, so the caller can feed it into
    `_vad_record`'s pre-roll instead of discarding it - otherwise the ~80ms
    of audio right after "Hey Jarvis" (which can already contain the start
    of the command, if the user doesn't pause) is silently lost.
    """
    wakeword_model.reset()  # clear buffers left over from the previous cycle
    logger.info("Uyku modunda... ('Hey Jarvis' bekleniyor)")

    wait_start = time.perf_counter()
    chunk_latencies: list[float] = []
    while True:
        chunk, overflowed = stream.read(WAKEWORD_CHUNK_SAMPLES)
        if overflowed:
            logger.warning("Giris tamponu tasti (overflow) - ses kaybi olabilir.")
        chunk = chunk.reshape(-1)

        infer_start = time.perf_counter()
        prediction = wakeword_model.predict(chunk)
        chunk_latencies.append(time.perf_counter() - infer_start)

        if prediction.get(WAKEWORD_MODEL_NAME, 0.0) >= WAKEWORD_THRESHOLD:
            trigger_latency = time.perf_counter() - wait_start
            avg_chunk_ms = 1000 * sum(chunk_latencies) / len(chunk_latencies)
            logger.info(
                "Wake word algilandi (skor=%.2f, bekleme=%.1fs, ort. chunk gecikmesi=%.1fms)",
                prediction[WAKEWORD_MODEL_NAME],
                trigger_latency,
                avg_chunk_ms,
            )
            return chunk


def _transcribe(audio: np.ndarray) -> Optional[str]:
    """Runs faster-whisper on already-recorded audio, logging transcription latency."""
    start = time.perf_counter()
    try:
        segments, _info = model.transcribe(
            audio,
            beam_size=5,
            # No fixed `language=` - let the model detect it per utterance.
            # `multilingual=True` re-runs language detection on every segment
            # instead of once for the whole clip, so a sentence that switches
            # between Turkish and English mid-way is still handled correctly.
            multilingual=True,
            initial_prompt="Merhaba Jarvis. Hello, system online. Nasılsın? Execute command.",
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )
        text = "".join(segment.text for segment in segments).strip()
    except Exception as exc:
        # A single bad turn (e.g. a transient CUDA/ctranslate2 error) must not
        # kill listen_loop()'s otherwise-infinite generator.
        logger.error("Transkripsiyon basarisiz, bu turn atlaniyor: %s", exc)
        return None

    logger.info("Transkripsiyon gecikmesi: %.2fs", time.perf_counter() - start)
    return text


def transcribe_once() -> Optional[str]:
    """Standalone single-shot capture: opens its own stream, records one VAD-bounded
    utterance and returns its transcript - no wake word required. Used for manual
    testing (see `.claude/skills/verify-audio-pipeline`) and as the __main__ entry point.
    """
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16") as stream:
            audio = _vad_record(stream)
    except sd.PortAudioError as exc:
        logger.error("Mikrofon acilamadi: %s", exc)
        return None

    if audio is None:
        return None
    return _transcribe(audio)


def listen_loop() -> Iterator[str]:
    """State machine over a single persistent stream: IDLE (wake-word) <-> ACTIVE (VAD
    capture + transcription). Continuously yields transcripts, one per utterance that
    followed a detected wake word. Silent/empty turns are skipped without leaving ACTIVE.
    """
    state = ListenState.IDLE
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16") as stream:
            while True:
                state = ListenState.IDLE
                trailing = _wait_for_wakeword(stream)

                state = ListenState.ACTIVE
                audio = _vad_record(stream, trailing=trailing)
                if audio is None:
                    continue
                text = _transcribe(audio)
                if text:
                    yield text
    except sd.PortAudioError as exc:
        logger.error("Mikrofon acilamadi (state=%s): %s", state.name, exc)


if __name__ == "__main__":
    result = transcribe_once()
    logger.info("Jarvis Heard: %s", result)
