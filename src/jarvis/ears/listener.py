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
import threading
import time
from collections import deque
from enum import Enum, auto
from typing import Callable, Iterator, Optional

import numpy as np
import sounddevice as sd
import webrtcvad
from faster_whisper import WhisperModel
from openwakeword.model import Model as WakeWordModel
from openwakeword.utils import download_models as download_wakeword_models

from src.jarvis.core.console import setup_logging

setup_logging()  # merkezi RichHandler kurulumu - bkz. core/console.py docstring'i
logger = logging.getLogger("jarvis.ears")

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480 samples @ 16kHz/30ms
SILENCE_HANGOVER_MS = 700
MAX_WAIT_MS = 10000  # how long to wait for speech to start before giving up
MAX_UTTERANCE_MS = 20000  # how long a triggered utterance may run before force-stopping
PREROLL_FRAMES = 3  # ~90ms of audio kept before trigger, so onset isn't clipped
VAD_AGGRESSIVENESS = 3  # 0 (permissive) - 3 (aggressive filtering of non-speech).
                         # 2026-08-29 (Cluster B): 2 -> 3. webrtcvad seviye 2, kisa
                         # gurultu darbelerine (klavye, tikirti, nefes) sik
                         # tetikleniyor ve faster-whisper'in kendi vad_filter'i
                         # sonra klibin %100'unu siliyordu (bos/halusinasyon
                         # turlar). Seviye 3 + MIN_SPEECH_FRAMES tabani bunlari
                         # kaynakta eler; yumusak baslangiclari PREROLL_FRAMES
                         # (~90ms) + whisper'in vad_filter'i telafi eder.
MIN_SPEECH_FRAMES = 3  # bir kaydin gecerli sayilmasi icin gereken GERCEK sesli
                        # (is_speech=True) frame sayisi - ~90ms. Yalnizca 1-2
                        # frame "konusma" sanilan kisacik bir blip (gurultu)
                        # transkripsiyona hic gitmez. "dur"/"ac"/"evet" gibi en
                        # kisa gercek komutlar bile bunu rahatca asar.
MIN_TRANSCRIBE_SECONDS = 0.35  # bu sureden kisa bir klip icin model.transcribe
                                # HIC cagrilmaz - net kazanc: bos turlarda
                                # gereksiz GPU cagrisi + halusinasyon riski yok.
FOLLOWUP_WINDOW_MS = 12000  # Jarvis konustuktan sonra wake-word gerekmeden devam
                             # konusmasi icin beklenen sure. MAX_WAIT_MS'ten (10s)
                             # biraz daha genis tutuldu cunku kullanici Jarvis'in
                             # cevabini dinleyip dusunmek icin ek zamana ihtiyac
                             # duyabilir. (Deger deneysel olarak ayarlanmali.)

WAKEWORD_MODEL_NAME = "hey_jarvis"
WAKEWORD_THRESHOLD = 0.5
WAKEWORD_CHUNK_MS = 80  # openWakeWord's native frame size; other sizes work but add latency
WAKEWORD_CHUNK_SAMPLES = SAMPLE_RATE * WAKEWORD_CHUNK_MS // 1000  # 1280 samples

CLAP_MIN_ABS_THRESHOLD = 800       # noise_floor cok dusukken (sessiz oda) esigin
                                    # gercekci-olmayan derecede dusmesini engelleyen
                                    # taban - tipik mikrofon oz-gurultusunun (~50-150
                                    # RMS) belirgin ustunde ama eski sabit esikten
                                    # (4000) cok daha dusuk, boylece uzak/hafif
                                    # alkislar da yakalanabilsin
CLAP_SENSITIVITY_MULTIPLIER = 6.0  # dinamik esik = noise_floor * bu deger; bir
                                    # "yuksek" sesin ortam gurultusunden en az ~6x
                                    # guclu olmasi gerekir - gurultu tabaninin dogal
                                    # kisa-vadeli dalgalanmasini (1.5-2x) rahatca
                                    # asiyor, gercek bir alkisi ise (uzaktan bile)
                                    # genelde kolayca gecer
CLAP_NOISE_FLOOR_EMA_ALPHA = 0.05  # ortam gurultusu EMA'sinin adaptasyon hizi
                                    # (~20 chunk / ~1.6s zaman sabiti @ 80ms/chunk) -
                                    # oda gurultusundeki yavas degisimi (klima acilip
                                    # kapanmasi vb.) takip eder. NOT: "yuksek"
                                    # (is_loud) sayilmak icin crest_factor >= 3.5 da
                                    # gerekiyor - surekli konusma/TV/muzik gibi
                                    # impulsif OLMAYAN seslerin crest factor'u dusuk
                                    # (~1.5-2) oldugundan bunlar "yuksek degil" sayilip
                                    # EMA'ya katilir; asagidaki CLAP_NOISE_FLOOR_MAX
                                    # bu yuzden gerekli (bkz. o sabitin aciklamasi).
CLAP_INITIAL_NOISE_FLOOR = 300.0   # EMA'nin baslangic tohumu - tipik sessiz-oda
                                    # int16 RMS duzeyi icin makul bir varsayim; EMA
                                    # hizla gercek degere yakinsar
CLAP_NOISE_FLOOR_MAX = 450.0       # EMA'nin ust siniri - uzun bir konusma/surekli
                                    # ortam sesi (crest factor dusuk oldugu icin
                                    # "yuksek degil" sayilip EMA'yi besler, yukaridaki
                                    # not) sinirlanmazsa noise_floor'u surukleyip
                                    # dinamik esigi (noise_floor*6) gercek alkislarin
                                    # RMS'inin (bu makinede gozlemlenen: ~3300-4800)
                                    # ustune cikarabiliyordu - kullanicinin bildirdigi
                                    # "once uzak/yakin fark etmeden calisiyordu, simdi
                                    # birkac deneme gerekiyor" sikayetinin kok nedeni
                                    # buydu. 450*6=2700 tavani, en zayif gozlemlenen
                                    # alkistan (3340) rahat payla altta kaliyor.
CLAP_MIN_CREST_FACTOR = 3.0        # "yuksek" bir chunk'in alkis benzeri (impulsif/
                                    # transient) sayilmasi icin gereken min. peak/RMS
                                    # orani. Surekli yuksek sesler (bagirma, muzik, TV)
                                    # daha duz bir zarfa sahiptir (crest factor ~1.5-2),
                                    # alkis gibi keskin darbeler ise cok daha yuksek bir
                                    # peak/RMS oranina sahiptir. Eskiden 3.5'ti;
                                    # gercek testte uzaktan (oda yankisi crest factor'u
                                    # dusurdugu icin) basarili bir alkis 4.1'de olculdu -
                                    # 3.5 bu tur sinirdaki uzak alkislari bazen reddedip
                                    # "birkac deneme" gerektiriyordu, 3.0'a dusurulerek
                                    # pay artirildi (surekli seslerin ~1.5-2'lik crest
                                    # factor'unden hala rahat ayirt ediliyor).
CLAP_MIN_GAP_MS = 150  # iki alkis arasi min. sure - tek bir alkisin yankisini/
                        # ikinci pikini yanlislikla "ikinci alkis" saymayi engeller.
                        # Eskiden 200ms'ti; gercek testte gecerli bir uzak cift-alkis
                        # 159ms'de bu yuzden reddedildi (near-miss logu bunu gosterdi) -
                        # kullanicinin doganl hizli alkis ritmi buna carpiyordu. 150ms'e
                        # dusuruldu; oda yankisi/tek alkis decay'i genelde bundan daha
                        # kisa surdugu icin yanlis-cift-sayma riski hala dusuk.
CLAP_MAX_GAP_MS = 800  # iki alkis arasi maks. sure - bu pencere asilirsa ilk
                        # alkis olarak yeniden sayilir (bekleyen durum sifirlanir)

_clap_noise_floor: float = CLAP_INITIAL_NOISE_FLOOR  # bilincli olarak modul-seviyesinde
    # ve _wait_for_wakeword() cagrilari arasinda KALICI - wakeword_model.reset()'in
    # aksine (o, model-ici tamponlari temizler ve her IDLE dongusunde sifirlanmasi
    # dogrudur), ortam gurultusu odanin bir ozelligidir; her cagride sifirlansaydi
    # EMA her seferinde soguktan yeniden yakinsamak zorunda kalir, "uzak/hafif alkis"
    # kazanimi buharlasirdi


class ListenState(Enum):
    IDLE = auto()  # waiting for the wake word, nothing is transcribed
    ACTIVE = auto()  # wake word triggered, VAD-recording an utterance
    FOLLOWUP = auto()  # an utterance just ended; briefly re-listening without the wake word


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
logger.info("faster-whisper modeli yukleniyor (turbo, ilk yuklemede birkac saniye surebilir)...")
model, _device = _load_model_with_fallback()
logger.info("faster-whisper '%s' cihazinda yuklendi.", _device)
logger.info("openWakeWord modeli yukleniyor...")
wakeword_model = _load_wakeword_model()
logger.info("openWakeWord '%s' modeli yuklendi.", WAKEWORD_MODEL_NAME)


def get_active_device() -> str:
    """faster-whisper'ın çalıştığı cihazı döndürür (`"cuda"`/`"cpu"`).

    `core/cli_commands.py`'nin `/status` komutu için - modül-seviyesi özel
    `_device`'ı (yukarıda `_load_model_with_fallback()` tarafından atanır)
    dışa açan tek satırlık bir erişimci.
    """
    return _device


def _vad_record(
    stream: sd.InputStream,
    trailing: Optional[np.ndarray] = None,
    max_wait_ms: int = MAX_WAIT_MS,
    stop_event: Optional[threading.Event] = None,
    mute_event: Optional[threading.Event] = None,
) -> Optional[np.ndarray]:
    """Records one utterance using VAD endpointing on an already-open stream.

    Returns float32 mono audio, or None if nothing was said before the wait
    timeout (or a shutdown was requested mid-recording, see `stop_event`).
    Takes an open stream (rather than opening its own) so the same
    microphone connection can be shared with wake-word listening - opening/
    closing the device on every state transition would add latency and risk
    dropping the first bit of audio.

    `trailing`, if given, is audio captured right after the wake word fired
    (see `_wait_for_wakeword`) that hasn't been classified by VAD yet. It's
    seeded into the pre-roll buffer so a command spoken with no pause after
    "Hey Jarvis" doesn't lose its first ~80ms.

    `max_wait_ms` overrides the default pre-speech wait timeout - used by
    `listen_loop()`'s follow-up window (FOLLOWUP_WINDOW_MS) so a continued
    conversation gets a different grace period than the initial wake-word
    trigger.

    `stop_event`, if given, is checked once per frame (~30ms) - the loop
    can't interrupt a blocking `stream.read()` already in flight, but frames
    are short enough that this bounds the worst-case shutdown latency to
    roughly one frame period rather than waiting for the full recording/
    timeout to finish naturally.

    `mute_event`, if given, Jarvis suanda konusurken set olur (turn-bazli -
    `core/app.py:run_jarvis()` tur boyunca sahiplenir, `mouth/tts.py:speak()`
    artik `manage_mute=False` ile cagrilir). Frame HALA okunur (buffer
    overflow'u onlemek icin) ama:
    - `triggered=False` (konusma BASLANGICI araniyor) iken: is_speech hic
      sorulmadan sessizlik sayilir - Jarvis'in kendi sesinin yeni bir
      "kullanici konusuyor" tetiklemesi onlenir.
    - `triggered=True` (kayit devam ediyor) iken bile: kayit iptal edilip
      None donulur (A1, 2026-08-29). Turn-bazli mute sayesinde Jarvis bir
      kullanicinin gercek konusmasinin ortasinda konusmaya baslamaz, bu
      yuzden mute'lu bir kayit = yanki; sonuna kadar kaydetmek (eski hali)
      cok-cumleli yanitlarda akustik feedback dongusune yol aciyordu.
    """
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    hangover_frames = SILENCE_HANGOVER_MS // FRAME_MS
    max_wait_frames = max_wait_ms // FRAME_MS
    max_speech_frames = MAX_UTTERANCE_MS // FRAME_MS

    preroll: deque[np.ndarray] = deque(maxlen=PREROLL_FRAMES)
    if trailing is not None:
        preroll.append(trailing)
    speech_frames: list[np.ndarray] = []
    triggered = False
    silence_run = 0
    wait_frames = 0
    speech_frame_count = 0
    voiced_frames = 0  # yalnizca is_speech=True olan frame'ler (preroll/hangover
                       # haric) - B3 (2026-08-29): kaydi kabul etmek icin bunun
                       # MIN_SPEECH_FRAMES'i asmasi gerekiyor.

    logger.info("Dinleniyor (konusmaya baslayabilirsiniz)...")

    while True:
        if stop_event is not None and stop_event.is_set():
            logger.info("Kapatma istendi, kayit iptal ediliyor.")
            return None
        if not triggered and wait_frames >= max_wait_frames:
            break
        if triggered and speech_frame_count >= max_speech_frames:
            break

        frame, overflowed = stream.read(FRAME_SAMPLES)
        if overflowed:
            logger.warning("Giris tamponu tasti (overflow) - ses kaybi olabilir.")
        frame = frame.reshape(-1)

        if mute_event is not None and mute_event.is_set():
            if triggered:
                # A1 (2026-08-29 optimizasyon turu): kayit ZATEN tetiklenmis
                # olsa bile Jarvis bu sirada konusmaya baslamissa (turn-bazli
                # mute, bkz. core/app.py:run_jarvis) yakalanan ses neredeyse
                # kesin hoparlorden sizan kendi TTS'idir - kaydi iptal et
                # (yanki bir kullanici turu olarak transkribe edilmesin).
                # Eski hali (sadece `not triggered` iken sessizlik saymak)
                # cok-cumleli yanitlarda cumleler arasi boslukta tetiklenip
                # devam eden kaydi durduramiyordu (canli test kok nedeni).
                logger.info("Jarvis konusurken ses algilandi - yanki sayilip kayit iptal edildi.")
                return None
            # Konusma baslangici araniyorken: bu frame'i VAD'a hic sormadan
            # sessizlik say (bkz. yukaridaki mute_event docstring notu).
            wait_frames += 1
            preroll.append(frame)
            continue

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
            voiced_frames += 1
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

    if voiced_frames < MIN_SPEECH_FRAMES:
        # B3 (2026-08-29): 1-2 frame "konusma" sanilan kisacik bir blip
        # (klavye/tikirti/nefes) - transkripsiyona hic gitme. Canli testte
        # onlarca "VAD filter removed 00:00.870 of audio" (bos tur) + arada
        # `initial_prompt` regurjitasyonu bunun sonucuydu.
        logger.info(
            "Kayit yeterince konusma icermiyor (%d < %d sesli frame) - atlandi.",
            voiced_frames,
            MIN_SPEECH_FRAMES,
        )
        return None

    logger.info("Kayit tamamlandi, transkribe ediliyor...")
    audio_int16 = np.concatenate(speech_frames)
    return audio_int16.astype(np.float32) / 32768.0


def _chunk_loudness(chunk: np.ndarray) -> tuple[float, float]:
    """Bir int16 ses chunk'inin RMS ve peak (mutlak tepe) genligini birlikte hesaplar.

    Crest factor (peak/RMS) icin ikisi de gerektiginden, ayni float32 cast'i
    uzerinden tek gecişte birlikte donduruluyor (int16 karesi overflow riski
    tasidigindan once float32'ye cast ediliyor).
    """
    samples = chunk.astype(np.float32)
    rms = float(np.sqrt(np.mean(samples ** 2)))
    peak = float(np.max(np.abs(samples)))
    return rms, peak


def _wait_for_wakeword(
    stream: sd.InputStream,
    stop_event: Optional[threading.Event] = None,
    mute_event: Optional[threading.Event] = None,
) -> Optional[np.ndarray]:
    """Blocks on an open stream until the wake word OR a double-clap is detected,
    logging latency. Both triggers are evaluated on the same stream of chunks -
    the RMS-based clap check is cheap enough to run every iteration alongside the
    openWakeWord inference, so no separate thread is needed.

    Returns the triggering chunk itself, so the caller can feed it into
    `_vad_record`'s pre-roll instead of discarding it - otherwise the ~80ms
    of audio right after the trigger (which can already contain the start
    of the command, if the user doesn't pause) is silently lost.

    Returns None if `stop_event` is set while waiting - this is a distinct
    sentinel from any real trigger (which always returns an ndarray), so
    `listen_loop()` can tell "shutdown requested" apart from "actually heard
    something" without ambiguity.

    `mute_event`, if given, is checked the same way as in `_vad_record()`
    (bkz. o fonksiyonun docstring'i) - set'ken chunk okunur (overflow'u
    onlemek icin) ama wake-word/alkis tespiti ve gurultu-tabani EMA
    guncellemesi hic yapilmadan atlanir, boylece Jarvis kendi sesiyle
    kendini "Hey Jarvis" veya alkis sanmaz.
    """
    global _clap_noise_floor
    wakeword_model.reset()  # clear buffers left over from the previous cycle
    logger.info("Uyku modunda... ('Hey Jarvis' veya cift alkis bekleniyor)")

    wait_start = time.perf_counter()
    chunk_latencies: list[float] = []
    clap_min_gap_s = CLAP_MIN_GAP_MS / 1000
    clap_max_gap_s = CLAP_MAX_GAP_MS / 1000
    last_clap_time: Optional[float] = None
    clap_active = False  # ayni alkisin birden fazla chunk'ta tekrar sayilmasini engeller
    while True:
        if stop_event is not None and stop_event.is_set():
            logger.info("Kapatma istendi, uyku modundan cikiliyor.")
            return None

        chunk, overflowed = stream.read(WAKEWORD_CHUNK_SAMPLES)
        if overflowed:
            logger.warning("Giris tamponu tasti (overflow) - ses kaybi olabilir.")
        chunk = chunk.reshape(-1)

        if mute_event is not None and mute_event.is_set():
            # Jarvis konusuyor - bkz. yukaridaki mute_event docstring notu.
            continue

        rms, peak = _chunk_loudness(chunk)
        crest_factor = peak / rms if rms > 1e-6 else 0.0
        dynamic_threshold = max(CLAP_MIN_ABS_THRESHOLD, _clap_noise_floor * CLAP_SENSITIVITY_MULTIPLIER)
        is_rms_loud = rms >= dynamic_threshold
        is_impulsive = crest_factor >= CLAP_MIN_CREST_FACTOR
        is_loud = is_rms_loud and is_impulsive

        if not is_rms_loud:
            # Gurultu tabanini SADECE gercekten sessiz/ortam-seviyesi chunk'lardan
            # guncelle (rms esigin altinda) - is_loud'un aksine, "yuksek ama impulsif
            # degil" (near-miss alkis/ani ses) chunk'lari da haric tutuyoruz; yoksa
            # bunlar da EMA'yi yukari surukleyip CLAP_NOISE_FLOOR_MAX'a ragmen esigi
            # gereksiz yukseltebilirdi. Ayrica CLAP_NOISE_FLOOR_MAX ile tavanlaniyor
            # (bkz. o sabitin aciklamasi - konusma gibi surekli ama dusuk-crest sesler
            # is_rms_loud'u da gecebilir, EMA'nin sinirsiz surunmesini onluyoruz).
            _clap_noise_floor = min(
                CLAP_NOISE_FLOOR_MAX,
                _clap_noise_floor + CLAP_NOISE_FLOOR_EMA_ALPHA * (rms - _clap_noise_floor),
            )
        elif not is_impulsive:
            # Yeterince yuksek ama yeterince "keskin" degil - uzaktan gelen bir alkis
            # (oda yankisi crest factor'u dusurur) veya impulsif olmayan bir gurultu
            # patlamasi (bagirma vb.) olabilir. G2 (2026-08-29): DEBUG seviyesine
            # cekildi - normal kullanimda (muzik/konusma acikken) her chunk'ta
            # basilip logu bogüyordu; CLAP_MIN_CREST_FACTOR ayari gerektiginde
            # `/debug` ile gorulebilir.
            logger.debug(
                "Yuksek ama yeterince impulsif degil (rms=%.0f, esik=%.0f, crest=%.1f, "
                "gereken=%.1f) - alkis sayilmadi.",
                rms, dynamic_threshold, crest_factor, CLAP_MIN_CREST_FACTOR,
            )

        if is_loud and not clap_active:
            now = time.perf_counter()
            if last_clap_time is not None and clap_min_gap_s <= now - last_clap_time <= clap_max_gap_s:
                logger.info(
                    "Cift alkis algilandi (rms=%.0f, esik=%.0f, crest=%.1f, alkis-arasi=%.0fms, bekleme=%.1fs)",
                    rms,
                    dynamic_threshold,
                    crest_factor,
                    (now - last_clap_time) * 1000,
                    now - wait_start,
                )
                return chunk
            if last_clap_time is not None:
                logger.debug(  # G2: near-miss, DEBUG (bkz. yukaridaki not)
                    "Yuksek+impulsif chunk algilandi ama cift alkis penceresi disinda "
                    "(rms=%.0f, crest=%.1f, onceki alkistan %.0fms sonra - pencere %d-%dms).",
                    rms,
                    crest_factor,
                    (now - last_clap_time) * 1000,
                    CLAP_MIN_GAP_MS,
                    CLAP_MAX_GAP_MS,
                )
            last_clap_time = now
        clap_active = is_loud

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


# faster-whisper'in TR/EN kod-degisimli (code-switching) dogrulugu icin verilen
# baglam ipucu. KORUNUYOR (2026-08-29 Cluster B karari) - iki dilli dogruluga
# katkisi var; ama whisper sessizlik/gurultude bu metni AYNEN geri kusabiliyor
# (canli testte `User: Hello, system online.`). Regurjitasyon `condition_on_previous_text=False`
# + asagidaki `_PROMPT_ECHO` blocklist'i ile kesiliyor.
_INITIAL_PROMPT = "Merhaba Jarvis. Hello, system online. Nasılsın? Execute command."

# P3 (2026-08-29 "pre-6.10 cilalama"): faster-whisper `hotwords` - decode'u
# Jarvis'in komut kelime dagarcigina yaklastirir. Canli testte "sesi kis" ->
# "Sesli kiz", "siradaki" -> "Stradik" gibi karismalar goruldu; bu liste o
# domeni bicimlendirir (initial_prompt'tan daha guclu, terime-ozgu bias).
# Yeni komut/arac eklendikce bu listeye de kelime eklenmeli.
_HOTWORDS = (
    "Jarvis sıradaki önceki şarkı parça çal durdur duraklat geç ses sesi "
    "seviye aç kıs azalt artır yükselt düşür yüzde not notlar liste listele "
    "ekle birleştir oluştur başlık proje sistem durum komut çalıştır uygulama "
    "Obsidian Spotify "
    "next previous track play pause stop skip volume level up down louder "
    "quieter percent note notes list append merge create title project system "
    "status run command launch app"
)

# `initial_prompt`'un birebir geri donen cumle parcalari + faster-whisper'in
# bos/gurultulu seste sik urettigi bilinen halusinasyonlar (hepsi lowercase,
# strip'li karsilastirilir).
_HALLUCINATION_PHRASES = {
    "merhaba jarvis.",
    "hello, system online.",
    "nasılsın?",
    "execute command.",
    "thank you.",
    "thank you very much.",
    "thanks for watching!",
    "altyazı m.k.",
    "amara.org",
    "abone ol",
}


def _is_probable_hallucination(text: str) -> bool:
    """Tum transkript (lowercase/strip) bilinen bir halusinasyon kalibiysa
    (initial_prompt echo'su dahil) True - segment-bazi guven gate'inden
    kacan tam-metin regurjitasyonlarini yakalar."""
    norm = text.strip().lower()
    return norm in _HALLUCINATION_PHRASES or norm == _INITIAL_PROMPT.strip().lower()


def _transcribe(audio: np.ndarray) -> Optional[str]:
    """Runs faster-whisper on already-recorded audio, logging transcription latency.

    Bos/gurultulu turlar icin cok katmanli gate (2026-08-29 Cluster B):
    (1) MIN_TRANSCRIBE_SECONDS altindaki klip -> model HIC cagrilmaz;
    (2) `no_speech_threshold`/`log_prob_threshold`/`compression_ratio_threshold`
        + segment-bazi `no_speech_prob`/`avg_logprob` esikleri -> guvensiz
        segmentler atilir;
    (3) tam-metin halusinasyon blocklist'i (`_is_probable_hallucination`).
    """
    if len(audio) < int(SAMPLE_RATE * MIN_TRANSCRIBE_SECONDS):
        logger.info(
            "Kayit cok kisa (%.2fs < %.2fs) - transkripsiyon atlandi.",
            len(audio) / SAMPLE_RATE,
            MIN_TRANSCRIBE_SECONDS,
        )
        return None

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
            initial_prompt=_INITIAL_PROMPT,
            hotwords=_HOTWORDS,  # P3: komut kelime dagarcigina bias
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
            # B1: onceki (halusinasyon olabilecek) metni bir sonraki decode'a
            # besleme - bir kez baslayan regurjitasyonu kendi kendine buyutuyordu.
            condition_on_previous_text=False,
            # B2: whisper'in kendi sessizlik/guven esikleri (varsayilanlara
            # sessizce guvenmek yerine acikca yaziyoruz).
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
        )
        kept: list[str] = []
        for segment in segments:
            no_speech = float(getattr(segment, "no_speech_prob", 0.0) or 0.0)
            avg_logprob = float(getattr(segment, "avg_logprob", 0.0) or 0.0)
            if no_speech > 0.6 and avg_logprob < -0.5:
                logger.info(
                    "Segment atlandi (no_speech=%.2f, avg_logprob=%.2f): %r",
                    no_speech,
                    avg_logprob,
                    segment.text.strip(),
                )
                continue
            kept.append(segment.text)
        text = "".join(kept).strip()
    except Exception as exc:
        # A single bad turn (e.g. a transient CUDA/ctranslate2 error) must not
        # kill listen_loop()'s otherwise-infinite generator.
        logger.error("Transkripsiyon basarisiz, bu turn atlaniyor: %s", exc)
        return None

    logger.info("Transkripsiyon gecikmesi: %.2fs", time.perf_counter() - start)
    if text and _is_probable_hallucination(text):
        logger.info("Muhtemel halusinasyon transkripti atlandi: %r", text)
        return None
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


def listen_loop(
    stop_event: Optional[threading.Event] = None,
    mute_event: Optional[threading.Event] = None,
    on_state_change: Optional[Callable[[str], None]] = None,
) -> Iterator[str]:
    """State machine over a single persistent stream: IDLE (wake-word) -> ACTIVE (VAD
    capture + transcription) -> FOLLOWUP (brief re-listen without wake word) -> back to
    ACTIVE if speech continues, or IDLE if the follow-up window times out. Continuously
    yields transcripts. Empty/unintelligible turns are NOT dropped back to IDLE - a
    misheard command doesn't force the user to re-trigger the wake word.

    The follow-up deadline is only RESET to a fresh FOLLOWUP_WINDOW_MS after a turn
    that actually produced text; empty turns (webrtcvad firing on ambient noise/breath
    with nothing for faster-whisper's own vad_filter to keep - a real, observed
    failure mode) just consume whatever time is left on the existing deadline instead
    of granting a brand new window each time. Otherwise a string of noise-triggered
    empty captures could keep re-arming a full window indefinitely, delaying the
    return to IDLE far longer than FOLLOWUP_WINDOW_MS (see docs/ROADMAP.md Faz 1.1).

    `stop_event`, if given, lets the caller (core.app.run_jarvis()) request a
    graceful shutdown: checked between state transitions AND (via `_wait_for_wakeword`/
    `_vad_record`) once per audio frame, so the generator stops yielding and this
    function returns - closing the InputStream via the `with` block - within roughly
    one frame period of the event being set, rather than only on an exception. Note
    this bounds LOOP-BOUNDARY latency, not a currently in-flight model call (e.g. a
    multi-second faster-whisper transcription already running won't be interrupted
    mid-call - see core.app.run_jarvis()'s docstring for the full caveat).

    `mute_event`, if given (bkz. `core/app.py:run_jarvis()`'in `mouth/tts.py:speak()`
    ile paylastigi ayni event), sadece `_wait_for_wakeword`/`_vad_record`'a
    aynen iletilir - Jarvis konusurken yeni bir tetiklemenin ARANMAMASI icin
    (bkz. o iki fonksiyonun kendi mute_event notlari). `listen_loop()` bunun
    disinda bu event'e dokunmaz/degistirmez, SADECE okur.

    `on_state_change`, if given (JARVIS HUD - web-ui entegrasyonu, bkz.
    `core/input_hub.py:_mic_producer`'in bunu `core/hud_bus.publish_state`
    ile dogrudan bagladigi yer), `state` degistigi HER noktada ("idle"/
    "listening" - ACTIVE ve FOLLOWUP ikisi de disaridan "dinliyor" olarak
    gorunur, ayirt edilmez) cagrilir. `stop_event`/`mute_event`'le AYNI
    "sadece opsiyonel bir kanca, bu modul ne oldugunu bilmez" deseninde -
    `ears/listener.py` `hud_bus`'i (veya baska bir ust katmani) import ETMEZ,
    sadece verilen callback'i cagirir (bagimlilik yonu her zaman yukari dogru).
    """
    state = ListenState.IDLE
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16") as stream:
            while stop_event is None or not stop_event.is_set():
                state = ListenState.IDLE
                if on_state_change is not None:
                    on_state_change("idle")
                trailing = _wait_for_wakeword(stream, stop_event=stop_event, mute_event=mute_event)
                if trailing is None:  # shutdown requested while waiting
                    break

                state = ListenState.ACTIVE
                if on_state_change is not None:
                    on_state_change("listening")
                audio = _vad_record(stream, trailing=trailing, stop_event=stop_event, mute_event=mute_event)
                # The turn that triggered ACTIVE always earns one full follow-up
                # window, whether or not it produced text - matches the previous
                # behavior for this first turn.
                followup_deadline = time.perf_counter() + FOLLOWUP_WINDOW_MS / 1000
                while audio is not None:
                    text = _transcribe(audio)
                    if text:
                        yield text
                        followup_deadline = time.perf_counter() + FOLLOWUP_WINDOW_MS / 1000

                    if stop_event is not None and stop_event.is_set():
                        break

                    state = ListenState.FOLLOWUP
                    if on_state_change is not None:
                        on_state_change("listening")
                    remaining_ms = int((followup_deadline - time.perf_counter()) * 1000)
                    if remaining_ms <= 0:
                        break
                    logger.info(
                        "Takip penceresi acik (kalan %.0fs) - 'Hey Jarvis' demeden devam edebilirsiniz...",
                        remaining_ms / 1000,
                    )
                    audio = _vad_record(
                        stream, max_wait_ms=remaining_ms, stop_event=stop_event, mute_event=mute_event
                    )

                if state is ListenState.FOLLOWUP:
                    logger.info("Takip penceresi zaman asimina ugradi, uyku moduna donuluyor.")
    except sd.PortAudioError as exc:
        logger.error("Mikrofon acilamadi (state=%s): %s", state.name, exc)
    logger.info("Ears kapatildi.")


if __name__ == "__main__":
    result = transcribe_once()
    logger.info("Jarvis Heard: %s", result)
