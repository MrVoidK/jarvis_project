import logging
import os
import queue
import threading
import time
from typing import Optional

import numpy as np
import sounddevice as sd

from src.jarvis.core.language import detect_language as _detect_language

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

from src.jarvis.core.console import setup_logging  # noqa: E402 - COQUI_TOS_AGREED'den sonra import edilmeli

# setup_logging() stdlib logging.basicConfig() gibi idempotent (ilk cagiran
# kazanir) - main.py uzerinden calisirken bu no-op olur (zaten kurulmustur),
# ama bu dosya tek basina (__main__) calistirildiginda logger.info()
# cagrilarinin sessizce kaybolmamasi icin burada da cagirmak gerekiyor.
setup_logging()
logger = logging.getLogger("jarvis.mouth")

XTTS_MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"
XTTS_SAMPLE_RATE = 24000  # XTTS-v2'nin sabit native cikis ornekleme hizi

# VRAM-optimize cift dilli TTS: TEK model instance'i, dile gore secilen IKI
# referans ses/embedding cifti (bkz. docs/ARCHITECTURE.md SS5) - ayri bir TR
# modeli yuklemek yerine ayni Xtts nesnesi hem EN hem TR conditioning latent'
# lerini tasir, VRAM maliyeti pratikte sifira yakin (birkac MB embedding).
REFERENCE_AUDIO_EN = "jarvis_reference.wav"  # zorunlu - zero-shot voice cloning icin ana referans (6-30sn onerilir)
REFERENCE_AUDIO_TR = "jarvis_reference_tr.wav"  # opsiyonel - yoksa Turkce icin de EN embedding'ine dusulur (asagida)

TTS_STREAM_CHUNK_SIZE = 20  # inference_stream()'in kutuphane varsayilanini acikca
                             # yaziyoruz - default'a sessizce guvenmek yerine, ileride
                             # tuning yapilacaksa neyin degistigi acik olsun diye
TTS_QUEUE_MAXSIZE = 8       # producer'in (uretim thread'i) consumer'dan (oynatma)
                             # en fazla kac chunk ileri gidebilecegini sinirlar -
                             # bellek sinirsiz buyumesin, ama gecici bir inference
                             # yavaslamasini yutacak kadar da pay birakir
TTS_PREBUFFER_CHUNKS = 3    # oynatma baslamadan once biriktirilecek chunk sayisi
                             # (jitter buffer) - ilk chunk'lar genelde en yavas
                             # uretilenlerdir (model isinmasi/artan attention context),
                             # bu pay ilk write()'larda ani duraklamayi onler

_TTS_STREAM_DONE = object()  # queue'ya "uretim basariyla bitti" isareti olarak
                              # konur - None/0 gibi gercek bir ses degeriyle
                              # karisma riski olmayan tekil bir nesne



VoiceProfile = tuple[torch.Tensor, torch.Tensor]  # (gpt_cond_latent, speaker_embedding)


def _compute_voice_profiles(model: Xtts) -> dict[str, VoiceProfile]:
    """Referans ses(ler)inden conditioning latent'leri hesaplar - EN zorunlu, TR opsiyonel.

    TR referansi (REFERENCE_AUDIO_TR) proje kokunde yoksa, "tr" anahtari da EN
    profiline dusurulur: ozellik hemen calisir durumda kalir (kullanici XTTS'in
    coklu-dil fonetik kontrolunu yine de kullanir, sadece klonlanan ses EN
    referansindan gelir), gercek bir TR dublaj ornegi eklendiginde kod
    degisikligi gerekmeden devreye girer.
    """
    profiles: dict[str, VoiceProfile] = {
        "en": model.get_conditioning_latents(audio_path=[REFERENCE_AUDIO_EN])
    }
    if os.path.isfile(REFERENCE_AUDIO_TR):
        profiles["tr"] = model.get_conditioning_latents(audio_path=[REFERENCE_AUDIO_TR])
    else:
        logger.warning(
            "Turkce referans ses bulunamadi (%s) - Turkce yanitlarda da EN sesine "
            "(%s) dusuluyor. Daha dogal bir TR aksani icin bu dosyayi ekleyin.",
            REFERENCE_AUDIO_TR,
            REFERENCE_AUDIO_EN,
        )
        profiles["tr"] = profiles["en"]
    return profiles


def _load_tts_model_with_fallback(
    model_dir: os.PathLike, config_path: os.PathLike
) -> tuple[Xtts, str, dict[str, VoiceProfile]]:
    """Loads XTTS-v2 and computes each reference voice's conditioning latents once,
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
            return model, "cuda", _compute_voice_profiles(model)
        except Exception as exc:
            logger.warning("CUDA kullanilamiyor (%s), CPU'ya dusuluyor.", exc)

    model.cpu()
    return model, "cpu", _compute_voice_profiles(model)


if not os.path.isfile(REFERENCE_AUDIO_EN):
    raise FileNotFoundError(
        f"Referans ses dosyasi bulunamadi: {REFERENCE_AUDIO_EN}. "
        "XTTS-v2 voice cloning icin proje kokune 6-30sn'lik temiz bir "
        "konusma ornegi eklenmeli."
    )

logger.info("XTTS-v2 modeli indiriliyor/yukleniyor (ilk calistirmada internet gerekebilir)...")
_load_start = time.perf_counter()
_model_dir, _config_path, _model_item = ModelManager().download_model(XTTS_MODEL_NAME)
model, _device, _voice_profiles = _load_tts_model_with_fallback(_model_dir, _config_path)
logger.info(
    "XTTS-v2 '%s' cihazinda yuklendi (%.1fs, diller: %s).",
    _device,
    time.perf_counter() - _load_start,
    ", ".join(sorted(_voice_profiles)),
)


def get_active_device() -> str:
    """XTTS-v2'nin çalıştığı cihazı döndürür (`"cuda"`/`"cpu"`).

    `core/cli_commands.py`'nin `/status` komutu için - `ears/listener.py:
    get_active_device()` ile simetrik, modül-seviyesi özel `_device`'ı dışa
    açan tek satırlık bir erişimci.
    """
    return _device


_PLAYBACK_LOCK = threading.Lock()  # speak() cagrilarinin ses cikisini seri hale
    # getirir - normal akista run_jarvis() zaten speak()'i tek tek, sirayla cagiriyor,
    # ama bu kilit gelecekte concurrent bir cagri yolu (veya ayni anda iki speak()
    # cagrisinin OutputStream'lerinin cakismasi - bazi Windows ses surucu/backend'
    # lerinde write()/close() teorik olarak beklendigi gibi tam senkron blok
    # etmeyebiliyor) durumunda iki sesin ust uste binmesini kesin olarak engeller.


def _produce_tts_chunks(
    text: str, lang: str, voice_profile: VoiceProfile, chunk_queue: "queue.Queue[object]"
) -> None:
    """inference_stream()'i ayri bir thread'de tuketen producer.

    GPU/CPU forward pass'i (bloklayici, GIL/GPU-agir) ile sd.OutputStream.write()'i
    (bloklayici IO) ayni thread'de calistirmak, herhangi bir inference yavaslamasinin
    dogrudan sese sizip duraklama/tikirti sesine yol acmasina neden oluyordu. Bu fonksiyon
    SADECE uretir; oynatma consumer tarafinda (speak() icinde, ana thread'de) yapilir.
    Basarili/basarisiz her iki durumda da queue'ya tam olarak bir "bitti" isareti konur
    (sentinel ya da exception nesnesinin kendisi) - consumer bunu gorunce durur.

    `voice_profile`, cagiran tarafin (speak()) diline gore _voice_profiles'tan
    sectigi (gpt_cond_latent, speaker_embedding) cifti - modul-global'e ortuk
    bagimlilik yerine acik parametre olarak aliniyor (tek sorumluluk).
    """
    gpt_cond_latent, speaker_embedding = voice_profile
    try:
        chunks = model.inference_stream(
            text, lang, gpt_cond_latent, speaker_embedding,
            stream_chunk_size=TTS_STREAM_CHUNK_SIZE,
        )
        for chunk in chunks:
            audio = chunk.detach().cpu().numpy().astype(np.float32).reshape(-1, 1)
            chunk_queue.put(audio)
    except Exception as exc:
        chunk_queue.put(exc)
    else:
        chunk_queue.put(_TTS_STREAM_DONE)


def speak(
    text: str, language: Optional[str] = None, stop_event: Optional[threading.Event] = None
) -> None:
    """Metni XTTS-v2 ile klonlanmis sesle senteler ve dogrudan hoparlore akitir.

    Gecici bir .wav dosyasi yazilmiyor: uretim (_produce_tts_chunks) ayri bir
    thread'de calisip chunk'lari bir queue'ya biriktiriyor, bu fonksiyon (consumer)
    kuyruktan okuyup sounddevice.OutputStream'e yaziyor (gercek streaming oynatma,
    tum cumle bitmeden baslar). Uretim ile oynatma boylece birbirinden ayrisiyor -
    kisa bir inference yavaslamasi, kuyrukta biriken chunk'lar (TTS_QUEUE_MAXSIZE)
    ve baslangictaki jitter buffer (TTS_PREBUFFER_CHUNKS) sayesinde sese sizmiyor.
    speak() cagiran tarafa gore hala senkron/blocking - roadmap'te barge-in zaten
    MVP disi birakildigi icin mevcut senkron run_jarvis() dongusuyle tutarli.

    `_PLAYBACK_LOCK` butun govdeyi sariyor - iki speak() cagrisinin sesleri asla
    ust uste binmez, cagiran taraf zaten sirali olsa da bu garanti kod-seviyesinde
    kesinlesir (bkz. modul-seviyesi tanim).

    `stop_event` verilirse (core.app.run_jarvis()'in graceful-shutdown mekanizmasi,
    bkz. ears/listener.py'deki ayni desen) uc noktada kontrol edilir: cagrildiginda
    zaten set edilmisse hic baslamadan don; on-bellek/oynatma dongulerinde her
    chunk'ta kontrol edilip set edilirse mevcut cumle yarida kesilip erken cikilir -
    kapatma sirasinda kalan tum kuyruklanmis sesin sonuna kadar beklenmez.
    """
    if not text:
        return

    if stop_event is not None and stop_event.is_set():
        return

    lang = language or _detect_language(text)
    # Iki dil profili (en/tr) arasindan secim: XTTS'e gecen fonetik "lang" kodu
    # tum SUPPORTED_LANGUAGES setini kapsayabilir, ama klonlanan SES sadece
    # EN/TR referanslari arasinda switch eder (bkz. docs/ARCHITECTURE.md SS5) -
    # "tr" disindaki her dil EN sesiyle okunur.
    voice_profile = _voice_profiles["tr"] if lang == "tr" else _voice_profiles["en"]
    start = time.perf_counter()
    first_chunk_logged = False

    with _PLAYBACK_LOCK:
        try:
            chunk_queue: "queue.Queue[object]" = queue.Queue(maxsize=TTS_QUEUE_MAXSIZE)
            producer = threading.Thread(
                target=_produce_tts_chunks, args=(text, lang, voice_profile, chunk_queue), daemon=True,
            )
            producer.start()

            try:
                # On-bellek (jitter buffer): oynatmayi baslatmadan once birkac chunk biriktir,
                # boylece ilk chunk'lardaki yavasligin sonraki write()'lara sizmasi engellenir.
                prebuffer: list[np.ndarray] = []
                terminal: Optional[object] = None
                shutting_down = False
                while len(prebuffer) < TTS_PREBUFFER_CHUNKS:
                    if stop_event is not None and stop_event.is_set():
                        shutting_down = True
                        break
                    item = chunk_queue.get()
                    if not first_chunk_logged:
                        logger.info("Ilk ses chunk'i hazir: %.2fs", time.perf_counter() - start)
                        first_chunk_logged = True
                    if item is _TTS_STREAM_DONE or isinstance(item, Exception):
                        terminal = item
                        break
                    prebuffer.append(item)

                if not shutting_down:
                    with sd.OutputStream(samplerate=XTTS_SAMPLE_RATE, channels=1, dtype="float32") as out:
                        for audio in prebuffer:
                            if stop_event is not None and stop_event.is_set():
                                shutting_down = True
                                out.abort()
                                break
                            out.write(audio)
                        while not shutting_down and terminal is None:
                            if stop_event is not None and stop_event.is_set():
                                shutting_down = True
                                # out.abort() (PortAudio Pa_AbortStream) zaten yazilmis
                                # tamponun CALINMASINI BEKLEMEDEN aninda durur - `with`
                                # bloğunun normal cikisinda cagrilan close()/stop()
                                # (Pa_StopStream) ise tam tersine kalan sesin bitmesini
                                # bekliyor; gercek testte bu yuzden shutdown ~3sn
                                # suruyordu (sesi hemen kesiyorduk ama speak() fonksiyon
                                # olarak PortAudio'nun stop() cagrisinda takili kaliyordu).
                                out.abort()
                                break
                            item = chunk_queue.get()
                            if item is _TTS_STREAM_DONE:
                                break
                            if isinstance(item, Exception):
                                terminal = item
                                break
                            out.write(item)

                if isinstance(terminal, Exception):
                    raise terminal
            finally:
                if shutting_down:
                    # Kapatma sirasinda producer'in (daemon=True) GPU inference'ini
                    # dogal olarak bitirmesini BEKLEMIYORUZ - process zaten kapanacagi
                    # icin thread process ile birlikte olecek. Beklemeye devam etmek
                    # (asagidaki normal-yol drain dongusu gibi) sesi aninda kesmemize
                    # ragmen speak()'in donusunu - dolayisiyla Ctrl+C sonrasi
                    # run_jarvis()'in kapanisini - kalan inference suresi kadar (bir
                    # kac saniye) gereksiz yere geciktiriyordu (gercek testte olculdu).
                    pass
                else:
                    # Consumer erken cikarsa (or. OutputStream/aygit hatasi) producer'i
                    # dolu queue.put()'ta sonsuza kadar bloklu birakmamak icin kuyrugu
                    # bosalt.
                    while producer.is_alive():
                        try:
                            chunk_queue.get_nowait()
                        except queue.Empty:
                            producer.join(timeout=0.1)
        except Exception as exc:
            # Tek bir kotu TTS turn'u (VRAM OOM, cihaz hatasi vb.) run_jarvis()'in
            # dongusunu cokertmemeli - _transcribe()'daki izolasyonla ayni desen.
            logger.error("TTS basarisiz, bu turn sessiz kaliniyor: %s", exc)
            return

        if shutting_down:
            logger.info("Kapatma istendi, TTS oynatmasi yarida kesildi.")
        else:
            logger.info(
                "Toplam sentez+oynatma suresi: %.2fs (dil=%s)", time.perf_counter() - start, lang
            )


if __name__ == "__main__":
    speak("Merhaba, ben Jarvis. Sistemler cevrimici.")
