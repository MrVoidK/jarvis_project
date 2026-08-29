"""ears/listener.py testleri - gerçek mikrofon/CUDA/model OLMADAN.

`_vad_record` ve `_transcribe` saf fonksiyonlar: birincisi bir "stream benzeri"
nesne (`.read(n) -> (ndarray, overflowed)`) ve `webrtcvad.Vad` bağımlılığı
üzerinden, ikincisi modül-global `model.transcribe` üzerinden test edilir -
ikisi de monkeypatch'lenebilir.

DİKKAT: `import src.jarvis.ears.listener` modül üstünde gerçek faster-whisper +
openWakeWord modellerini YÜKLER (bkz. o dosyanın üst seviyesi). Bu test dosyası
CI'da/hızlı test turunda ağır - `-m "not slow"` gibi bir işaretle ayrılması
düşünülebilir; şimdilik `pytest tests/` ağır yüklemeyi bir kez öder.

Calistirma: `python -m pytest tests/test_listener.py -v` (repo kokunden).
"""

import threading

import numpy as np
import pytest

from src.jarvis.ears import listener


class _AlwaysSpeechVad:
    """webrtcvad.Vad yerine: her frame'i 'konuşma' say."""

    def __init__(self, *_args):
        pass

    def is_speech(self, _buf, _rate) -> bool:
        return True


class _FakeStream:
    """`_vad_record`'ın beklediği minimum arayüz: `.read(n) -> (int16 ndarray, bool)`.

    `on_read`, her okuma çağrısında read sayacıyla çağrılır (test bir noktada
    mute_event'i set etmek gibi yan etkiler için kullanır).
    """

    def __init__(self, on_read=None):
        self._n = 0
        self._on_read = on_read

    def read(self, frames):
        self._n += 1
        if self._on_read is not None:
            self._on_read(self._n)
        return np.zeros((frames, 1), dtype=np.int16), False


class _SeqVad:
    """webrtcvad.Vad yerine: önceden verilen `is_speech` dizisini sırayla döner,
    dizi bitince `False`."""

    _seq: list = []

    def __init__(self, *_args):
        self._i = 0

    def is_speech(self, _buf, _rate) -> bool:
        value = self._seq[self._i] if self._i < len(self._seq) else False
        self._i += 1
        return value


class _FakeSegment:
    def __init__(self, text, no_speech_prob=0.0, avg_logprob=0.0):
        self.text = text
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob


class _FakeInfo:
    duration = 1.0
    language = "en"
    language_probability = 0.9


def _one_second_of_silence() -> np.ndarray:
    return np.zeros(int(listener.SAMPLE_RATE * 1.0), dtype=np.float32)


def test_vad_record_aborts_when_muted_after_trigger(monkeypatch):
    """A1: kayıt bir kez tetiklendikten (triggered=True) sonra bile, Jarvis
    konuşmaya başlarsa (mute_event set) kayıt YANKI sayılıp iptal edilmeli
    (None döner) - yoksa Jarvis'in kendi TTS'i kullanıcı turu olarak kaydedilir.
    """
    monkeypatch.setattr(listener.webrtcvad, "Vad", _AlwaysSpeechVad)
    mute = threading.Event()

    def _mute_on_third_read(n: int) -> None:
        if n == 3:  # ilk 2 frame tetikler, 3.'te Jarvis konuşmaya başladı
            mute.set()

    result = listener._vad_record(
        _FakeStream(on_read=_mute_on_third_read),
        stop_event=threading.Event(),
        mute_event=mute,
    )
    assert result is None


def test_vad_record_treats_mute_as_silence_before_trigger(monkeypatch):
    """A1 değişmezi: mute baştan set ve hiç tetiklenmediyse, mevcut davranış
    korunur - konuşma başlangıcı aranmaz, pencere dolunca None döner."""
    monkeypatch.setattr(listener.webrtcvad, "Vad", _AlwaysSpeechVad)
    mute = threading.Event()
    mute.set()

    result = listener._vad_record(
        _FakeStream(),
        max_wait_ms=300,  # testi kısa tut
        stop_event=threading.Event(),
        mute_event=mute,
    )
    assert result is None


def test_vad_record_rejects_short_blip(monkeypatch):
    """B3: yalnızca 1-2 frame 'konuşma' sayılan kısacık bir blip (gürültü)
    MIN_SPEECH_FRAMES eşiğinin altında kalır → None (transkripsiyona hiç
    gitmez). Canli testte ~870ms'lik bos kayitlarin kok nedeni buydu."""
    monkeypatch.setattr(_SeqVad, "_seq", [True, True] + [False] * 40)
    monkeypatch.setattr(listener.webrtcvad, "Vad", _SeqVad)

    result = listener._vad_record(
        _FakeStream(), max_wait_ms=600, stop_event=threading.Event()
    )
    assert result is None


def test_vad_record_accepts_enough_voiced_frames(monkeypatch):
    """B3 karşı-testi: MIN_SPEECH_FRAMES kadar gerçek sesli frame varsa kayıt
    kabul edilir (ndarray döner)."""
    monkeypatch.setattr(_SeqVad, "_seq", [True] * 6 + [False] * 40)
    monkeypatch.setattr(listener.webrtcvad, "Vad", _SeqVad)

    result = listener._vad_record(
        _FakeStream(), max_wait_ms=600, stop_event=threading.Event()
    )
    assert result is not None


def test_transcribe_skips_audio_shorter_than_floor(monkeypatch):
    """B3: eşiğin (MIN_TRANSCRIBE_SECONDS) altındaki bir klip için model.transcribe
    HİÇ çağrılmaz."""
    calls = {"n": 0}

    def _fake_transcribe(*_a, **_k):
        calls["n"] += 1
        return iter(()), _FakeInfo()

    monkeypatch.setattr(listener.model, "transcribe", _fake_transcribe)
    short = np.zeros(int(listener.SAMPLE_RATE * 0.2), dtype=np.float32)  # 200ms

    assert listener._transcribe(short) is None
    assert calls["n"] == 0


def test_transcribe_drops_low_confidence_segment(monkeypatch):
    """B2: yüksek no_speech_prob + düşük avg_logprob segment atılır."""
    monkeypatch.setattr(
        listener.model,
        "transcribe",
        lambda *_a, **_k: (
            iter([_FakeSegment(" hayalet metin", no_speech_prob=0.92, avg_logprob=-1.6)]),
            _FakeInfo(),
        ),
    )
    assert (listener._transcribe(_one_second_of_silence()) or "") == ""


def test_transcribe_keeps_confident_segment(monkeypatch):
    """B2 karşı-testi: güvenli segment (düşük no_speech_prob) korunur."""
    monkeypatch.setattr(
        listener.model,
        "transcribe",
        lambda *_a, **_k: (
            iter([_FakeSegment(" saat kac", no_speech_prob=0.04, avg_logprob=-0.25)]),
            _FakeInfo(),
        ),
    )
    assert (listener._transcribe(_one_second_of_silence()) or "").strip() == "saat kac"


def test_apply_stt_corrections_command_context():
    # E (2026-08-29): bilinen whisper bozulmalari komut baglaminda duzeltilir.
    assert listener._apply_stt_corrections("Servis sesli kız") == "Jarvis sesi kıs"
    assert listener._apply_stt_corrections("Servis, sesli çok aç") == "Jarvis, sesi çok aç"
    assert listener._apply_stt_corrections("stradik şarkıya geç") == "sıradaki şarkıya geç"
    assert listener._apply_stt_corrections("çarkıyı devam ettir") == "şarkıyı devam ettir"
    assert listener._apply_stt_corrections("sesi azıcık kıs") == "sesi az kıs"


def test_apply_stt_corrections_preserves_normal_words():
    # "servis" cumle ici, "sesli" tek basina -> DOKUNMA
    assert listener._apply_stt_corrections("müzik servisi güzel") == "müzik servisi güzel"
    assert listener._apply_stt_corrections("bu sesli bir dosya") == "bu sesli bir dosya"


def test_transcribe_applies_stt_corrections(monkeypatch):
    monkeypatch.setattr(
        listener.model,
        "transcribe",
        lambda *_a, **_k: (
            iter([_FakeSegment("Servis sesli kız", no_speech_prob=0.05, avg_logprob=-0.2)]),
            _FakeInfo(),
        ),
    )
    assert listener._transcribe(_one_second_of_silence()) == "Jarvis sesi kıs"


def test_transcribe_passes_hotwords(monkeypatch):
    """P3: `_transcribe` komut kelime dağarcığını `hotwords` olarak geçirir."""
    captured: dict = {}

    def _fake(audio, **kwargs):
        captured.update(kwargs)
        return iter([_FakeSegment(" sıradaki şarkı", no_speech_prob=0.05, avg_logprob=-0.2)]), _FakeInfo()

    monkeypatch.setattr(listener.model, "transcribe", _fake)
    listener._transcribe(_one_second_of_silence())

    assert captured.get("hotwords") == listener._HOTWORDS
    assert "sıradaki" in listener._HOTWORDS and "volume" in listener._HOTWORDS


def test_transcribe_drops_initial_prompt_regurgitation(monkeypatch):
    """B1/B2: whisper sessizlikte `initial_prompt`'u aynen geri kusarsa
    (`User: Hello, system online.` canli test) atılır."""
    monkeypatch.setattr(
        listener.model,
        "transcribe",
        lambda *_a, **_k: (
            iter([_FakeSegment("Hello, system online.", no_speech_prob=0.2, avg_logprob=-0.4)]),
            _FakeInfo(),
        ),
    )
    assert listener._transcribe(_one_second_of_silence()) is None
