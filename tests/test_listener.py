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
