"""JARVIS HUD (web-ui) icin Vite dev sunucusunu yoneten kucuk yardimci.

NEDEN AYRI BIR MODUL (core/api.py DEGIL): `core/api.py`'nin SRP siniri
"asyncio/FastAPI SADECE burada" (bkz. o dosyanin docstring'i) - bir OS
alt-surecini (npm/node) baslatip/durdurmak farkli bir sorumluluk, o dosyaya
karistirilmadi.

NEDEN `subprocess.Popen` + Windows-ozel kapatma: kullanici artik `npm run
dev`'i ELLE, ayri bir terminalde calistirmak ISTEMIYOR - `main.py` bunu
otomatik, arka planda bir alt-surec olarak baslatmali VE Ctrl+C ile Jarvis
kapandiginda bu alt-surec de KAPANMALI (eskiden: kullanici ayri terminali
elle kapatmak zorundaydi, unutulursa yetim bir `node.exe` calismaya devam
ediyordu). Windows'ta `npm` gercek bir PE degil bir `.cmd` sarmalayicidir;
`Popen.terminate()` SADECE bu `.cmd`'yi calistiran `cmd.exe`'yi durdurur,
onun COCUGU olan gercek `node.exe`/vite surecini DEGIL (bilinen bir Windows
subprocess tuzagi) - bu yuzden `stop_web_ui_dev_server()` `taskkill /T /F`
kullanarak TUM sürec agacini kapatiyor.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from src.jarvis.core.paths import PROJECT_ROOT

logger = logging.getLogger("jarvis.core.web_ui_process")

WEB_UI_DIR = Path(PROJECT_ROOT) / "web-ui"


def start_web_ui_dev_server() -> Optional[subprocess.Popen]:
    """`web-ui/`de `npm run dev`'i arka plan alt-sureci olarak baslatir.

    On kosullar (web-ui/ klasoru veya `npm install` hic calistirilmamis)
    saglanmiyorsa sessizce `None` doner - Jarvis'in geri kalani (Ears/
    Mouth/Brain, HUD API) bir on-uc olmadan da calisabilmeli, bu YUZDEN
    HATA FIRLATILMIYOR (bkz. cagiran main.py'nin bunu nasil ele aldigi).
    """
    if not WEB_UI_DIR.is_dir():
        logger.warning("web-ui/ klasoru bulunamadi (%s) - HUD web arayuzu atlaniyor.", WEB_UI_DIR)
        return None
    if not (WEB_UI_DIR / "node_modules").is_dir():
        logger.warning(
            "web-ui/node_modules bulunamadi - 'npm install' calistirilmamis, HUD web arayuzu atlaniyor."
        )
        return None

    npm_path = shutil.which("npm")
    if npm_path is None:
        logger.warning("npm PATH'te bulunamadi - HUD web arayuzu atlaniyor.")
        return None

    # `CREATE_NEW_PROCESS_GROUP`: Ctrl+C (CTRL_C_EVENT) Windows'ta TUM
    # console process group'una yayilir - bu bayrak olmadan alt-surec de
    # Jarvis'le AYNI anda, KENDI temizligimizden ONCE SIGINT alip
    # taskkill'in bulacagi PID'yi zaten olu birakabilirdi (yarisa acik).
    # Alt-sureci KENDI grubuna koyup kapatmayi biz `taskkill /T /F` ile
    # ACIKCA yapmak, sirayi/sonucu ongorulebilir kiliyor.
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0

    try:
        process = subprocess.Popen(
            [npm_path, "run", "dev"],
            cwd=str(WEB_UI_DIR),
            creationflags=creationflags,
        )
    except OSError as exc:
        logger.warning("HUD web arayuzu baslatilamadi: %s", exc)
        return None

    return process


def stop_web_ui_dev_server(process: Optional[subprocess.Popen]) -> None:
    """`start_web_ui_dev_server()`'in dondurdugu sureci (VARSA) TUM alt
    surecleriyle (npm -> node -> vite) birlikte kapatir - bkz. modul
    docstring'indeki Windows `.cmd` notu."""
    if process is None or process.poll() is not None:
        return  # zaten yok / zaten kapanmis

    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
