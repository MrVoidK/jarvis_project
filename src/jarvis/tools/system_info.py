"""Sistem izleme araci - CPU/RAM (psutil) + GPU/VRAM (nvidia-smi sarmalayicisi).

RTX 4070'in VRAM butcesi bu projede gercek bir kisit (bkz. docs/ARCHITECTURE.md SS5:
Whisper + Ollama + XTTS ayni anda ~8.5-10GB) - bu arac, o butcenin canli durumunu
Jarvis'e sorabilmek icin.
"""

import logging
import subprocess

import psutil

from src.jarvis.core.risk import RiskLevel
from src.jarvis.tools.base import Tool

logger = logging.getLogger("jarvis.tools.system_info")

NVIDIA_SMI_TIMEOUT_S = 5

_TEMPLATES = {
    "tr": "Islemci yuzde {cpu:.0f}, bellek yuzde {ram:.0f} kullanimda{gpu}.",
    "en": "CPU is at {cpu:.0f} percent, memory at {ram:.0f} percent{gpu}.",
}
_GPU_TEMPLATES = {
    "tr": ", ekran karti yuzde {util:.0f} ve {used:.0f} megabayt VRAM kullaniyor",
    "en": ", the GPU is at {util:.0f} percent using {used:.0f} megabytes of VRAM",
}


def _query_gpu() -> tuple[float, float] | None:
    """nvidia-smi ile (GPU kullanimi %, kullanilan VRAM MB) doner; GPU yoksa None.

    nvidia-smi bulunamamasi (GPU'suz makine) beklenen bir durum - CPU/RAM bilgisi
    yine de dondurulebilsin diye burada sessizce None'a dusuluyor, hata degil.
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


class SystemInfoTool(Tool):
    """CPU/RAM/GPU durumunu tek cumlelik bir ozet olarak dondurur (salt-okunur)."""

    name = "get_system_info"
    description = "Islemci, bellek ve ekran karti kullanimini bildirir."
    risk_level = RiskLevel.LOW

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        # interval=0.1: psutil'in ilk cagrisi interval'siz her zaman 0.0 doner
        # (onceki cagriyla arasindaki farki olcuyor) - kisa bir olcum penceresi
        # gercek bir deger almak icin gerekli.
        cpu = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory().percent

        gpu_text = ""
        gpu = _query_gpu()
        if gpu is not None:
            util, used = gpu
            gpu_text = _GPU_TEMPLATES.get(lang, _GPU_TEMPLATES["en"]).format(
                util=util, used=used
            )

        template = _TEMPLATES.get(lang, _TEMPLATES["en"])
        return template.format(cpu=cpu, ram=ram, gpu=gpu_text)
