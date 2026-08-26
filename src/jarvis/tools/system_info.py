"""Sistem izleme araci - CPU/RAM/GPU okumasi icin bkz. core/telemetry.py.

RTX 4070'in VRAM butcesi bu projede gercek bir kisit (bkz. docs/ARCHITECTURE.md SS5:
Whisper + Ollama + XTTS ayni anda ~8.5-10GB) - bu arac, o butcenin canli durumunu
Jarvis'e sorabilmek icin.

Okuma mantigi (psutil + nvidia-smi) `core/telemetry.py`'ye tasindi - JARVIS HUD
(web-ui) de AYNI okumaya ihtiyac duyar (periyodik telemetri pollingi icin),
mantigi burada tekrar etmek yerine tek bir yerden paylasiliyor (DRY).
"""

from src.jarvis.core import telemetry
from src.jarvis.core.risk import RiskLevel
from src.jarvis.tools.base import Tool

_TEMPLATES = {
    "tr": "Islemci yuzde {cpu:.0f}, bellek yuzde {ram:.0f} kullanimda{gpu}.",
    "en": "CPU is at {cpu:.0f} percent, memory at {ram:.0f} percent{gpu}.",
}
_GPU_TEMPLATES = {
    "tr": ", ekran karti yuzde {util:.0f} ve {used:.0f} megabayt VRAM kullaniyor",
    "en": ", the GPU is at {util:.0f} percent using {used:.0f} megabytes of VRAM",
}


class SystemInfoTool(Tool):
    """CPU/RAM/GPU durumunu tek cumlelik bir ozet olarak dondurur (salt-okunur)."""

    name = "get_system_info"
    description = "Islemci, bellek ve ekran karti kullanimini bildirir."
    risk_level = RiskLevel.LOW

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        t = telemetry.read_system_telemetry()

        gpu_text = ""
        if t.gpu_util_percent is not None:
            gpu_text = _GPU_TEMPLATES.get(lang, _GPU_TEMPLATES["en"]).format(
                util=t.gpu_util_percent, used=t.gpu_vram_used_mb
            )

        template = _TEMPLATES.get(lang, _TEMPLATES["en"])
        return template.format(cpu=t.cpu_percent, ram=t.ram_percent, gpu=gpu_text)
