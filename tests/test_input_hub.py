"""InputHub testleri - gerçek mikrofon/stdin OLMADAN.

`InputHub.__init__()` sadece `threading.Thread` NESNELERİ oluşturur,
`.start()` çağrılmadıkça hiçbir gerçek mic/stdin thread'i başlamaz - bu
yüzden kuyruk/pending mantığı, `hub._queue`'ya elle sahte `InputEvent`
konularak (ve `wait_for_text_answer`'ın "metin thread'i canlı mı" kontrolü
için gerektiğinde zararsız bir yer tutucu thread atanarak) izole test
edilebilir (bkz. `src/jarvis/core/input_hub.py` modül docstring'i -
`_mic_producer` içindeki gecikmeli `ears.listener` importu sayesinde bu
dosyayı import etmek bile gerçek model yüklemesi tetiklemez).

Calistirma: `python -m pytest tests/ -v` (repo kokunden, bkz. CLAUDE.md Komutlar).
"""

import queue
import sys
import threading
import time

from src.jarvis.core.input_hub import InputEvent, InputHub


def _idle_hub() -> InputHub:
    """Thread'leri HİÇ başlatmadan bir InputHub döner - `_queue` doğrudan
    elle beslenebilir."""
    return InputHub(threading.Event())


def _mark_text_thread_alive(hub: InputHub) -> threading.Thread:
    """`wait_for_text_answer()`nin 'metin thread'i hâlâ canlı mı' kontrolünü
    geçmesi için gerçek ama zararsız bir thread atar (test-only yardımcı)."""
    placeholder = threading.Thread(target=lambda: time.sleep(2), daemon=True)
    placeholder.start()
    hub._text_thread = placeholder
    return placeholder


def test_next_event_returns_queued_item_in_order():
    hub = _idle_hub()
    hub._queue.put(InputEvent(source="voice", text="ilk"))
    hub._queue.put(InputEvent(source="text", text="ikinci"))

    assert hub.next_event() == InputEvent(source="voice", text="ilk")
    assert hub.next_event() == InputEvent(source="text", text="ikinci")


def test_wait_for_text_answer_ignores_items_queued_before_the_call(monkeypatch=None):
    # security-reviewer bulgusu: onay paneli gosterilmeden ONCE kuyrukta
    # bekleyen bir metin, kullanici o paneli hic gormeden "cevap" sayilamaz.
    # text thread'i CANLI DEGIL (baslatilmadi) - bu yuzden kuyruk bosalinca
    # fonksiyon varsayilan RED'e ("") dusmeli, ONCEKI metni cevap SAYMAMALI.
    hub = _idle_hub()
    hub._queue.put(InputEvent(source="text", text="onay panelinden once yazilmis, alakasiz"))
    hub._queue.put(InputEvent(source="voice", text="onay panelinden once soylenmis, alakasiz"))

    pending: list[InputEvent] = []
    answer = hub.wait_for_text_answer(pending, poll_interval=0.05)

    assert answer == ""  # onceki metin cevap sayilmadi
    assert len(pending) == 2  # ikisi de (text dahil) pending'e aktarildi


def test_wait_for_text_answer_accepts_genuinely_new_text_event():
    hub = _idle_hub()
    _mark_text_thread_alive(hub)

    def _deliver_later() -> None:
        time.sleep(0.1)
        hub._queue.put(InputEvent(source="text", text="evet"))

    threading.Thread(target=_deliver_later, daemon=True).start()

    answer = hub.wait_for_text_answer(pending=[], poll_interval=0.05)

    assert answer == "evet"


def test_wait_for_text_answer_defers_new_voice_events_to_pending():
    # Onay bekleme sirasinda GELEN (kuyrukta ONCEDEN olmayan) "voice"
    # olaylari CEVAP SAYILMAMALI (sesli onay bilincli olarak devre disi,
    # bkz. core/risk.py) - pending'e eklenip SONRA islenmek uzere
    # saklanmali, kaybolmamali.
    hub = _idle_hub()
    _mark_text_thread_alive(hub)

    def _deliver_later() -> None:
        time.sleep(0.05)
        hub._queue.put(InputEvent(source="voice", text="alakasiz bir seyler soyledim"))
        time.sleep(0.05)
        hub._queue.put(InputEvent(source="voice", text="baska bir sey daha"))
        time.sleep(0.05)
        hub._queue.put(InputEvent(source="text", text="y"))

    threading.Thread(target=_deliver_later, daemon=True).start()

    pending: list[InputEvent] = []
    answer = hub.wait_for_text_answer(pending, poll_interval=0.05)

    assert answer == "y"
    assert pending == [
        InputEvent(source="voice", text="alakasiz bir seyler soyledim"),
        InputEvent(source="voice", text="baska bir sey daha"),
    ]


def test_wait_for_text_answer_defaults_to_reject_when_text_thread_is_dead():
    # Metin thread'i (stdin'in TEK sahibi) EOFError ile sonlanmissa, bir
    # daha ASLA yeni bir "text" olayi gelmeyecek - sonsuza kadar beklemek
    # (tam bir DoS + "kapali stdin = ret" ilkesinin ihlali) yerine
    # varsayilan RED'e dusmeli.
    hub = _idle_hub()
    # hub._text_thread hic baslatilmadi -> is_alive() False.

    pending: list[InputEvent] = []
    answer = hub.wait_for_text_answer(pending, poll_interval=0.05)

    assert answer == ""
    assert pending == []


def test_input_event_is_a_plain_value_object():
    a = InputEvent(source="text", text="merhaba")
    b = InputEvent(source="text", text="merhaba")
    assert a == b


def test_input_hub_accepts_optional_speaking_event():
    # core/app.py:run_jarvis()'in speak()/InputHub arasinda paylastigi mikrofon-
    # susturma event'i - verilmezse (eski cagiranlar/testler icin geriye donuk
    # uyumlu) InputHub kendi bos Event()'ini kullanir, hic hata vermez.
    hub_without = InputHub(threading.Event())
    assert isinstance(hub_without._speaking_event, threading.Event)
    assert not hub_without._speaking_event.is_set()

    speaking_event = threading.Event()
    hub_with = InputHub(threading.Event(), speaking_event)
    assert hub_with._speaking_event is speaking_event


def test_mic_producer_forwards_speaking_event_to_listen_loop_as_mute_event(monkeypatch):
    # `input_hub.py:_mic_producer` listen_loop()'u `ears.listener` icinden
    # gecikmeli import ediyor (bkz. modulun kendi yorumu) - sys.modules'a sahte
    # bir modul enjekte ederek gercek mikrofon/model yuklemesini tetiklemeden
    # dogru event'in `mute_event=` olarak iletildigini dogruluyoruz.
    import types

    from src.jarvis.core.input_hub import _mic_producer

    captured = {}

    def fake_listen_loop(stop_event=None, mute_event=None):
        captured["stop_event"] = stop_event
        captured["mute_event"] = mute_event
        return iter(())  # hicbir transkript uretmeden hemen biter

    fake_listener_module = types.ModuleType("src.jarvis.ears.listener")
    fake_listener_module.listen_loop = fake_listen_loop
    monkeypatch.setitem(sys.modules, "src.jarvis.ears.listener", fake_listener_module)

    stop_event = threading.Event()
    speaking_event = threading.Event()
    _mic_producer(queue.Queue(), stop_event, speaking_event)

    assert captured["stop_event"] is stop_event
    assert captured["mute_event"] is speaking_event
