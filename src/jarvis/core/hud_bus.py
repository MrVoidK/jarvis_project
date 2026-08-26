"""JARVIS HUD (web-ui) icin thread-safe pub/sub olay veriyolu.

NEDEN GEREKLI: `core/console.py`, `mouth/tts.py`, `ears/listener.py`,
`core/app.py` gibi mevcut tum yayinci kod SENKRON/dogrudan thread'lerde
calisiyor (ana thread, mic thread, TTS oynatma thread'i) - hicbiri asyncio
bilmiyor ve bilmemeli (core/api.py disindaki hicbir modul FastAPI/asyncio'ya
bagimli olmamali, SRP). Ote yandan her WebSocket baglantisi kendi asyncio
event loop'unda calisiyor. Bu modul, "sync thread -> N tane asyncio loop"
koprusunu TEK bir merkezde kurar; geri kalan tum kod sadece `publish(...)`
cagirir, WebSocket/asyncio'nun var oldugunu bile bilmez.

NEDEN `queue.put_nowait()` DEGIL `loop.call_soon_threadsafe(...)`: bir
`asyncio.Queue`, kendi event loop'unu calistiran thread DISINDAN dogrudan
`put_nowait()` ile mutasyona ugratilirsa ic `deque`/bekleyen coroutine
uyandirma mantigi kilitsiz oldugu icin veri yarisina (race condition) acik -
`asyncio`'nun kendi dokumantasyonunun onerdigi tek guvenli yol, hedef loop'un
KENDI thread'inde calisacak sekilde `call_soon_threadsafe` ile bir callback
zamanlamak (o callback da ayni loop'ta oldugu icin `put_nowait` orada guvenli).

`queue.Queue` (bu modulun DEGIL, `core/input_hub.py`'nin kullandigi) bunun
tam tersi bir durum - o zaten stdlib'in kendi kilidiyle her thread'den
guvenli, bu yuzden `InputHub.submit_external_text()` boyle bir koprüye
ihtiyac duymuyor (bkz. o fonksiyonun docstring'i).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from dataclasses import asdict, is_dataclass
from typing import Optional

logger = logging.getLogger("jarvis.core.hud_bus")

_LOG_RING_SIZE = 100

_lock = threading.Lock()
_subscribers: list[tuple[asyncio.AbstractEventLoop, "asyncio.Queue[dict]"]] = []
_log_ring: "deque[dict]" = deque(maxlen=_LOG_RING_SIZE)
_last_state: str = "idle"


def subscribe(loop: asyncio.AbstractEventLoop) -> "asyncio.Queue[dict]":
    """Yeni bir WebSocket baglantisi icin abone kuyrugu olusturur.

    `queue`nun kendisi SADECE `loop` calisirken tuketilmeli - `publish()`
    bu kuyruga her zaman `loop.call_soon_threadsafe` uzerinden yazar, asla
    dogrudan degil (bkz. modul docstring'i).
    """
    queue: "asyncio.Queue[dict]" = asyncio.Queue()
    with _lock:
        _subscribers.append((loop, queue))
    return queue


def unsubscribe(queue: "asyncio.Queue[dict]") -> None:
    """Baglanti kapandiginda (veya publish sirasinda loop'u kapali bulununca)
    aboneyi listeden cikarir - kapali bir loop'a sonsuza kadar publish
    denemeye devam etmemek icin."""
    with _lock:
        _subscribers[:] = [(loop, q) for loop, q in _subscribers if q is not queue]


def recent_logs(limit: int = _LOG_RING_SIZE) -> list[dict]:
    """Son `limit` kadar `"log"` olayini dondurur - yeni baglanan bir
    tarayicinin, sunucu onceden (ör. main.py'nin acilis mesajlarini
    basarken) yayinlanmis satirlari `"snapshot"` uzerinden gormesi icin."""
    with _lock:
        return list(_log_ring)[-limit:]


def last_state() -> str:
    """Son yayinlanan `"state"` degeri - yeni baglanan bir tarayiciya
    ilk `"snapshot"`ta gonderilir ki NeuralCore dogru durumda acilsin."""
    with _lock:
        return _last_state


def publish(event: dict) -> None:
    """Herhangi bir sync thread'den cagrilir. Abone yoksa (yaygin durum -
    hicbir tarayici bagli degilken) sadece ring-buffer guncellemesiyle
    UCUZ bir no-op'tur.

    `event`e `"ts"` (epoch ms) burada, TEK noktadan eklenir - cagiran
    kod zaman damgasiyla ugrasmaz.
    """
    event = {"ts": int(time.time() * 1000), **event}

    global _last_state
    with _lock:
        if event.get("type") == "log":
            _log_ring.append(event)
        elif event.get("type") == "state":
            _last_state = event.get("state", _last_state)
        subscribers_snapshot = list(_subscribers)

    if not subscribers_snapshot:
        return

    for loop, queue in subscribers_snapshot:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, event)
        except RuntimeError:
            # Loop zaten kapatilmis (tarayici sekmesi kapandi, henuz
            # unsubscribe cagrilmamis) - bu abone kalici olarak olu,
            # sessizce cikar (bir sonraki publish'te tekrar denemeye
            # gerek yok).
            logger.debug("Kapali bir event loop'a publish denendi, abone cikariliyor.")
            unsubscribe(queue)


# ---- Tipli yardimcilar: geri kalan kod dogrudan dict insa etmez ----


def publish_log(kind: str, message: str, *, title: Optional[str] = None) -> None:
    """Konsol ciktisi icin - bkz. core/console.py'nin her print_* fonksiyonu."""
    event: dict = {"type": "log", "kind": kind, "message": message}
    if title is not None:
        event["title"] = title
    publish(event)


def publish_state(state: str) -> None:
    """NeuralCore'un durumu icin - "idle"/"listening"/"processing"/"speaking".
    Cagiranlar: ears/listener.py (on_state_change callback'i uzerinden),
    mouth/tts.py:speak(), core/app.py:run_jarvis()."""
    publish({"type": "state", "state": state})


def publish_telemetry(telemetry) -> None:
    """`core/telemetry.py:SystemTelemetry` dataclass'ini oldugu gibi JSON'a
    cevirip yayinlar - dataclass'i BURADA import etmiyoruz (gecikmeli/duck-
    typing: `is_dataclass` kontrolu), cunku telemetry.py zaten bagimsiz,
    hud_bus'a bagimli olmamali (dongusel import riski yaratmamak icin -
    ikisi de core/ altinda ama telemetry.py'nin hud_bus'i BILMESI gerekmiyor,
    sadece api.py'nin ikisini de kullanmasi yeterli)."""
    data = asdict(telemetry) if is_dataclass(telemetry) else dict(telemetry)
    publish({"type": "telemetry", **data})


def publish_tool(phase: str, name: str, *, params: Optional[dict] = None, result: Optional[str] = None) -> None:
    """Bir aracin baslama/bitis anini yayinlar - bkz. core/app.py:_execute_tool()."""
    event: dict = {"type": "tool", "phase": phase, "name": name}
    if params is not None:
        event["params"] = params
    if result is not None:
        event["result"] = result
    publish(event)
