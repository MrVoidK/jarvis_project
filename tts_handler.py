import logging
import os
import time
from typing import Optional

import numpy as np
import sounddevice as sd

# XTTS-v2 (Coqui) CPML lisansi altinda; ilk indirmede interaktif bir
# onay istemi cikariyor. Jarvis kisisel/ticari-olmayan bir asistan oldugu
# icin burada bilincli olarak otomatik kabul ediliyor - importlardan once
# ayarlanmali cunku onay kontrolu import zincirinin icinde yapiliyor.
os.environ.setdefault("COQUI_TOS_AGREED", "1")

import soundfile as sf  # noqa: E402
import torch  # noqa: E402 - COQUI_TOS_AGREED'den sonra import edilmeli
import torchaudio  # noqa: E402
from TTS.tts.configs.xtts_config import XttsConfig  # noqa: E402
from TTS.tts.models.xtts import Xtts  # noqa: E402
from TTS.utils.manage import ModelManager  # noqa: E402


def _load_audio_via_soundfile(path: str, *_args, **_kwargs) -> tuple[torch.Tensor, int]:
    """torchaudio.load()'un yerini alan monkeypatch.

    torch 2.9+'ta torchaudio.load()'in varsayilan backend'i torchcodec'e
    tasindi; torchcodec ise sistemde ayrica kurulu bir paylasimli FFmpeg
    kutuphanesi gerektiriyor (bu makinede yok, pip de bunu getirmiyor -
    torchcodec kendi native .dll'lerini sistemin FFmpeg'ine dinamik
    bagliyor). XTTS, referans .wav'i okumak icin sadece torchaudio.load()'u
    cagirdigi icin (get_conditioning_latents -> load_audio), burada zaten
    coqui-tts'in kendi bagimliligi olan `soundfile` (libsndfile, FFmpeg
    gerektirmez) ile ayni (tensor[kanal, ornek], sample_rate) sozlesmesini
    taklit ediyoruz - XTTS'in kendi load_audio()'su mono/resample islemini
    zaten bu ciktinin ustune uyguluyor, burada tekrar etmiyoruz.
    """
    data, samplerate = sf.read(path, dtype="float32", always_2d=True)
    waveform = torch.from_numpy(data.T)  # (samples, kanal) -> (kanal, samples)
    return waveform, samplerate


torchaudio.load = _load_audio_via_soundfile

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
# basicConfig() sadece root logger'da hic handler yoksa etkili olur (Python
# stdlib'in belgeledigi idempotent davranis) - main.py uzerinden calisirken
# audio_handler.py'nin cagrisi burada no-op olur, ama tts_handler.py tek
# basina (__main__) calistirildiginda logger.info() cagrilarinin sessizce
# kaybolmamasi icin burada da cagirmak gerekiyor.
logger = logging.getLogger("jarvis.mouth")

XTTS_MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"
XTTS_SAMPLE_RATE = 24000  # XTTS-v2'nin sabit native cikis ornekleme hizi
REFERENCE_AUDIO_PATH = "jarvis_reference.wav"  # zero-shot voice cloning icin referans ses (6-30sn onerilir)

# XTTS'in desteklegi dil kodlari (inference_stream'in "language" parametresi icin gecerli set)
_SUPPORTED_LANGUAGES = {
    "en", "es", "fr", "de", "it", "pt", "pl", "tr", "ru", "nl",
    "cs", "ar", "zh-cn", "ja", "ko", "hu", "hi",
}


def _load_tts_model_with_fallback(
    model_dir: os.PathLike, config_path: os.PathLike
) -> tuple[Xtts, str, torch.Tensor, torch.Tensor]:
    """Loads XTTS-v2 and computes the reference speaker's conditioning latents once,
    falling back to CPU if CUDA fails - mirrors audio_handler.py's
    _load_model_with_fallback(). Unlike that function's throwaway warm-up call,
    the "warm-up" inference here (get_conditioning_latents) IS the real
    speaker-embedding computation speak() needs anyway, so nothing is wasted.
    Also unlike ctranslate2, torch's Windows wheel bundles its own CUDA DLLs,
    so no os.add_dll_directory fix is needed here.
    """
    config = XttsConfig()
    config.load_json(config_path)
    model = Xtts.init_from_config(config)
    model.load_checkpoint(config, checkpoint_dir=model_dir, eval=True)

    if torch.cuda.is_available():
        try:
            model.cuda()
            gpt_cond_latent, speaker_embedding = model.get_conditioning_latents(
                audio_path=[REFERENCE_AUDIO_PATH]
            )
            return model, "cuda", gpt_cond_latent, speaker_embedding
        except Exception as exc:
            logger.warning("CUDA kullanilamiyor (%s), CPU'ya dusuluyor.", exc)

    model.cpu()
    gpt_cond_latent, speaker_embedding = model.get_conditioning_latents(
        audio_path=[REFERENCE_AUDIO_PATH]
    )
    return model, "cpu", gpt_cond_latent, speaker_embedding


if not os.path.isfile(REFERENCE_AUDIO_PATH):
    raise FileNotFoundError(
        f"Referans ses dosyasi bulunamadi: {REFERENCE_AUDIO_PATH}. "
        "XTTS-v2 voice cloning icin proje kokune 6-30sn'lik temiz bir "
        "konusma ornegi eklenmeli."
    )

logger.info("XTTS-v2 modeli indiriliyor/yukleniyor (ilk calistirmada internet gerekebilir)...")
_load_start = time.perf_counter()
_model_dir, _config_path, _model_item = ModelManager().download_model(XTTS_MODEL_NAME)
model, _device, _gpt_cond_latent, _speaker_embedding = _load_tts_model_with_fallback(
    _model_dir, _config_path
)
logger.info(
    "XTTS-v2 '%s' cihazinda yuklendi (%.1fs, referans: %s).",
    _device,
    time.perf_counter() - _load_start,
    REFERENCE_AUDIO_PATH,
)


def _detect_language(text: str) -> str:
    """Metnin dilini tespit eder, XTTS'in desteklemedigi/belirsiz bir sonucta "en"'e duser.

    main.py'deki SYSTEM_PROMPT su an yanitlari Ingilizce'ye sabitliyor, yani bu
    fonksiyon pratikte hep "en" donuyor - ama Brain ileride cok dilli olursa
    tts_handler.py'de ek degisiklik gerekmeyecek sekilde hazir birakiliyor.
    """
    from langdetect import LangDetectException, detect

    try:
        lang = detect(text)
    except LangDetectException:
        return "en"
    if lang.startswith("zh"):
        return "zh-cn"
    return lang if lang in _SUPPORTED_LANGUAGES else "en"


def speak(text: str, language: Optional[str] = None) -> None:
    """Metni XTTS-v2 ile klonlanmis sesle senteler ve dogrudan hoparlore akitir.

    Gecici bir .wav dosyasi yazilmiyor: inference_stream()'in urettigi her
    chunk, geldigi anda sounddevice.OutputStream'e yaziliyor (gercek streaming
    oynatma, tum cumle bitmeden baslar). Senkron/blocking - roadmap'te
    barge-in zaten MVP disi birakildigi icin mevcut senkron run_jarvis()
    dongusuyle tutarli.
    """
    if not text:
        return

    lang = language or _detect_language(text)
    start = time.perf_counter()
    first_chunk_logged = False

    try:
        chunks = model.inference_stream(text, lang, _gpt_cond_latent, _speaker_embedding)
        with sd.OutputStream(samplerate=XTTS_SAMPLE_RATE, channels=1, dtype="float32") as out:
            for chunk in chunks:
                if not first_chunk_logged:
                    logger.info("Ilk ses chunk'i hazir: %.2fs", time.perf_counter() - start)
                    first_chunk_logged = True
                audio = chunk.detach().cpu().numpy().astype(np.float32).reshape(-1, 1)
                out.write(audio)
    except Exception as exc:
        # Tek bir kotu TTS turn'u (VRAM OOM, cihaz hatasi vb.) run_jarvis()'in
        # dongusunu cokertmemeli - _transcribe()'daki izolasyonla ayni desen.
        logger.error("TTS basarisiz, bu turn sessiz kaliniyor: %s", exc)
        return

    logger.info("Toplam sentez+oynatma suresi: %.2fs (dil=%s)", time.perf_counter() - start, lang)


if __name__ == "__main__":
    speak("Merhaba, ben Jarvis. Sistemler cevrimici.")
