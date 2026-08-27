"""JARVIS HUD (web-ui) icin FastAPI + WebSocket koprusu.

NEDEN ASYNC/FASTAPI TEK BURADA: `core/hud_bus.py`'nin docstring'inde
aciklandigi gibi, projenin geri kalani (ears/mouth/core/app) tamamen
senkron/thread tabanli - asyncio'yu SADECE bu dosya bilir (SRP). `main.py`
bu modulun `start_api_server_thread()`'ini AYRI bir daemon thread'de
cagirir - boylece uvicorn'un kendi event loop'u, faster-whisper/XTTS/Ollama
gibi senkron/bloklayici cagrilarin calistigi ana thread'i HICBIR ZAMAN
bloklamaz (iki thread arasindaki TEK ortak durum `hud_bus.py` uzerinden -
thread-safe pub/sub, bkz. o modulun "neden call_soon_threadsafe" notu).

GUVENLIK (iki katman):
1. Sunucu SADECE 127.0.0.1'e baglanir (LAN/internete acik degil - Jarvis
   tek-kullanicili/yerel bir asistan, bkz. `core/cli_commands.py:_cmd_test`
   docstring'indeki ayni tehdit modeli notu).
2. WebSocket handshake'inde `Origin` basligi elle dogrulaniyor
   (`_is_allowed_origin`). `CORSMiddleware` BILINCLI OLARAK bunun icin
   YETERLI DEGIL: tarayicilar WebSocket el sikismalarina normal fetch/XHR
   CORS politikasini UYGULAMAZ (preflight yok, Access-Control-* basliklari
   yoksayilir) - `CORSMiddleware` sadece normal HTTP rotalarini korur.
   Bu kontrol olmadan, kullanicinin ac{tigi herhangi bir sekmedeki KOTU
   NIYETLI bir web sitesi (Origin kontrolu olmayan bir yerel WebSocket
   sunucusuna karsi klasik "localhost'a DNS rebinding / CSRF-benzeri"
   saldirisi) bu soket'e baglanip `submit_external_text` uzerinden
   Guardrail/Dispatcher/Tool zincirine kadar rastgele komut sokabilirdi.
   Tarayici-disi (Origin göndermeyen) istemciler - ör. yerel test scripti -
   BILINCLI OLARAK reddedilmiyor (tek-kullanicili yerel arac, bkz. (1));
   asil savunma tarayicidan gelen SAHTE origin'i engellemek.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from src.jarvis.core import hud_bus
from src.jarvis.core.telemetry import read_static_system_info, read_system_telemetry

logger = logging.getLogger("jarvis.core.api")

API_HOST = "127.0.0.1"
API_PORT = 8000

# Vite dev sunucusunun BILINEN adresleri - bkz. web-ui/vite.config.ts
# (host/port burayla AYNI kalacak sekilde sabitlendi, "strictPort: true").
_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

_TELEMETRY_INTERVAL_S = 1.0


def _is_allowed_origin(origin: Optional[str]) -> bool:
    # Origin basligi YOKSA (tarayici-disi istemci) BILINCLI OLARAK izin
    # veriliyor - bkz. modul docstring'i "(2)" son paragraf.
    return origin is None or origin in _ALLOWED_ORIGINS


@asynccontextmanager
async def _lifespan(_: FastAPI):
    async def _telemetry_loop() -> None:
        while True:
            telemetry = await asyncio.to_thread(read_system_telemetry)
            hud_bus.publish_telemetry(telemetry)
            await asyncio.sleep(_TELEMETRY_INTERVAL_S)

    task = asyncio.create_task(_telemetry_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Jarvis HUD Bridge", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# `run_jarvis()` (core/app.py) hub'i olusturur olusturmaz `register_input_hub()`
# ile burayi doldurur - modul-seviyesinde `None` (henuz kayit olmamis) ihtimali
# HER ZAMAN var (ör. API sunucusu thread'i, Ears/Mouth modelleri henuz
# yuklenmeden ONCE ayaga kalkabilir), bu yuzden `_submit_text` bunu kontrol eder.
_input_hub_lock = threading.Lock()
_input_hub = None


def register_input_hub(hub) -> None:
    """`core/app.py:run_jarvis()` icin - WebSocket'ten gelen metnin ana
    girdi kuyruguna ulasmasinin TEK yolu (bkz. `input_hub.py:InputHub.
    submit_external_text()` docstring'i)."""
    global _input_hub
    with _input_hub_lock:
        _input_hub = hub


def _submit_text(text: str) -> None:
    with _input_hub_lock:
        hub = _input_hub
    if hub is None:
        logger.warning("WebSocket'ten metin geldi ama InputHub henuz kayıtlı değil, yoksayılıyor.")
        return
    hub.submit_external_text(text)


def _static_info_payload() -> dict:
    info = read_static_system_info()
    return {
        "cpu_model": info.cpu_model,
        "cpu_cores_physical": info.cpu_cores_physical,
        "cpu_cores_logical": info.cpu_cores_logical,
        "ram_total_gb": info.ram_total_gb,
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    if not _is_allowed_origin(websocket.headers.get("origin")):
        logger.warning("WebSocket reddedildi - izin verilmeyen Origin: %s", websocket.headers.get("origin"))
        await websocket.close(code=1008)
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    queue = hud_bus.subscribe(loop)

    try:
        static_info = await asyncio.to_thread(_static_info_payload)
        await websocket.send_json(
            {
                "type": "snapshot",
                "ts": int(time.time() * 1000),
                "state": hud_bus.last_state(),
                "logs": hud_bus.recent_logs(),
                "static_info": static_info,
            }
        )

        async def _receive_loop() -> None:
            while True:
                text = await websocket.receive_text()
                _submit_text(text)

        async def _send_loop() -> None:
            while True:
                event = await queue.get()
                await websocket.send_json(event)

        receive_task = asyncio.create_task(_receive_loop())
        send_task = asyncio.create_task(_send_loop())
        try:
            await asyncio.wait({receive_task, send_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            receive_task.cancel()
            send_task.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        hud_bus.unsubscribe(queue)


def run_api_server() -> None:
    """`start_api_server_thread()`'in daemon thread'i icinde calisir - bu
    cagri BLOKLAYICI (uvicorn kendi event loop'unu burada baslatir), bu
    yuzden asla ana thread'den DOGRUDAN cagrilmaz."""
    import uvicorn

    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level="warning")


def start_api_server_thread() -> threading.Thread:
    thread = threading.Thread(target=run_api_server, name="jarvis-hud-api", daemon=True)
    thread.start()
    return thread
