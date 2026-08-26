"""Sistem telemetrisi - psutil (+ opsiyonel nvidia-smi) tek noktadan okunur.

`tools/system_info.py:SystemInfoTool` (sesli/yazili "sistem durumu nedir"
sorusu) VE `core/api.py`'nin HUD icin periyodik pollingi AYNI fonksiyonlari
kullanir - GPU sorgulama mantigi eskiden SADECE system_info.py icinde ozel
(`_query_gpu`) idi, iki cagiran ortaya cikinca buraya tasindi (DRY).

SAHTE VERI YOK ILKESI: referans HUD tasarimindaki "13 ms latency" ve "47°C"
gibi alanlar BILINCLI OLARAK burada YOK - ping hedefi olmadan gercek bir
"latency" olcmek anlamsiz, `psutil.sensors_temperatures()` ise stok
Windows'ta (vendor WMI surucusu olmadan) guvenilir sonuc vermiyor. Gercek
karsiligi olmayan bir sayiyi uydurmaktansa alani hic gostermemek tercih
edildi.
"""

import logging
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

import psutil

logger = logging.getLogger("jarvis.core.telemetry")

NVIDIA_SMI_TIMEOUT_S = 5


@dataclass
class SystemTelemetry:
    """Periyodik (saniyede ~1) olarak degisen degerler."""

    cpu_percent: float
    ram_percent: float
    gpu_util_percent: Optional[float]
    gpu_vram_used_mb: Optional[float]
    net_up_kbps: Optional[float]
    net_down_kbps: Optional[float]


@dataclass
class StaticSystemInfo:
    """Surec boyunca degismeyen degerler - tek seferlik okunur."""

    cpu_model: str
    cpu_cores_physical: Optional[int]
    cpu_cores_logical: Optional[int]
    ram_total_gb: float


def _query_gpu() -> Optional[tuple[float, float]]:
    """nvidia-smi ile (GPU kullanimi %, kullanilan VRAM MB) doner; GPU yoksa None.

    `tools/system_info.py`'den DEGISTIRILMEDEN tasindi (bkz. modul docstring'i).
    """
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=NVIDIA_SMI_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.info("nvidia-smi kullanilamadi (%s) - GPU bilgisi atlaniyor.", exc)
        return None

    if result.returncode != 0 or not result.stdout.strip():
        logger.info("nvidia-smi bilgi dondurmedi - GPU bilgisi atlaniyor.")
        return None

    first_gpu = result.stdout.strip().splitlines()[0]
    try:
        util_str, used_str = (part.strip() for part in first_gpu.split(","))
        return float(util_str), float(used_str)
    except ValueError:
        logger.warning("nvidia-smi ciktisi beklenmedik bicimde: %r", first_gpu)
        return None


# EMA/onceki-ornek deseni `ears/listener.py:_clap_noise_floor` ile AYNI
# gerekce: KB/s hesaplamak icin iki olcum arasindaki byte farkina ihtiyac
# var, bu da cagrilar arasinda KALICI bir onceki-ornek gerektirir.
_last_net_sample: Optional[tuple[float, int, int]] = None  # (zaman, bytes_sent, bytes_recv)


def _read_net_kbps() -> tuple[Optional[float], Optional[float]]:
    global _last_net_sample
    counters = psutil.net_io_counters()
    now = time.monotonic()

    if _last_net_sample is None:
        _last_net_sample = (now, counters.bytes_sent, counters.bytes_recv)
        return None, None

    prev_time, prev_sent, prev_recv = _last_net_sample
    elapsed = now - prev_time
    _last_net_sample = (now, counters.bytes_sent, counters.bytes_recv)
    if elapsed <= 0:
        return None, None

    up_kbps = (counters.bytes_sent - prev_sent) / 1024 / elapsed
    down_kbps = (counters.bytes_recv - prev_recv) / 1024 / elapsed
    return max(up_kbps, 0.0), max(down_kbps, 0.0)


def read_system_telemetry() -> SystemTelemetry:
    """CPU/RAM/GPU/ag kullanimini tek seferde okur.

    `psutil.cpu_percent(interval=0.1)` BLOKLAYICIDIR (bkz. tools/system_info.py'nin
    ayni notu) - bir asyncio event loop icinden cagiran taraf (core/api.py)
    bunu `asyncio.to_thread(...)` ile SARMALAMALI, aksi halde 100ms boyunca
    o loop'taki TUM WebSocket okuma/yazmalari donar.
    """
    cpu = psutil.cpu_percent(interval=0.1)
    ram = psutil.virtual_memory().percent

    gpu_util: Optional[float] = None
    gpu_vram: Optional[float] = None
    gpu = _query_gpu()
    if gpu is not None:
        gpu_util, gpu_vram = gpu

    net_up, net_down = _read_net_kbps()

    return SystemTelemetry(
        cpu_percent=cpu,
        ram_percent=ram,
        gpu_util_percent=gpu_util,
        gpu_vram_used_mb=gpu_vram,
        net_up_kbps=net_up,
        net_down_kbps=net_down,
    )


def read_static_system_info() -> StaticSystemInfo:
    """Surec boyunca sabit kalan donanim bilgisi - tek seferlik cagrilmali
    (ör. HUD'un ilk "snapshot" mesaji icin), her telemetri turunda degil."""
    import platform

    return StaticSystemInfo(
        cpu_model=platform.processor() or "Bilinmiyor",
        cpu_cores_physical=psutil.cpu_count(logical=False),
        cpu_cores_logical=psutil.cpu_count(logical=True),
        ram_total_gb=round(psutil.virtual_memory().total / (1024**3), 1),
    )
