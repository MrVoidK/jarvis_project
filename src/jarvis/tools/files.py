"""Dosya listeleme araci - SADECE izole bir calisma dizini icinde.

Zero-Trust: Jarvis'in rastgele bir sistem dizinini listelemesine izin verilmiyor
(bkz. docs/ROADMAP.md Faz 3.1 "erisim izin verilen dizinlerle sinirli olmali").
Kullanici bir yol soyleyemez; arac her zaman WORKSPACE_DIR'e bakar - dolayisiyla
path traversal (../../) gibi bir saldiri yuzeyi hic olusmuyor, cunku disaridan
gelen hicbir yol parametresi yok.
"""

import logging
import os

from src.jarvis.core.paths import PROJECT_ROOT
from src.jarvis.core.risk import RiskLevel
from src.jarvis.tools.base import Tool

logger = logging.getLogger("jarvis.tools.files")

# Mutlak yol (CWD'ye bagimli DEGIL) - bkz. core/paths.py
WORKSPACE_DIR = os.path.join(PROJECT_ROOT, "jarvis_workspace")

LIST_FILES_LIMIT = 10  # TTS'e okunacagi icin uzun listeler kirpiliyor

_EMPTY_MESSAGES = {
    "tr": "Calisma dizininiz bos.",
    "en": "Your workspace is empty.",
}
_LIST_TEMPLATES = {
    "tr": "Calisma dizininde su dosyalar var: {files}.",
    "en": "Your workspace contains: {files}.",
}
_MORE_TEMPLATES = {"tr": " ve {n} dosya daha", "en": " and {n} more"}


class ListFilesTool(Tool):
    """WORKSPACE_DIR icerigini listeler (salt-okunur)."""

    name = "list_files"
    description = "Jarvis'in calisma dizinindeki dosyalari listeler."
    risk_level = RiskLevel.LOW

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
        entries = sorted(os.listdir(WORKSPACE_DIR))

        if not entries:
            return _EMPTY_MESSAGES.get(lang, _EMPTY_MESSAGES["en"])

        shown = entries[:LIST_FILES_LIMIT]
        text = _LIST_TEMPLATES.get(lang, _LIST_TEMPLATES["en"]).format(
            files=", ".join(shown)
        )
        if len(entries) > LIST_FILES_LIMIT:
            more = _MORE_TEMPLATES.get(lang, _MORE_TEMPLATES["en"])
            text = text.rstrip(".") + more.format(n=len(entries) - LIST_FILES_LIMIT) + "."

        logger.info("Calisma dizini listelendi (%d dosya).", len(entries))
        return text
