"""Not alma araclari - tek dosyaya (notes/notes.txt) zaman damgali, append-only satirlar.

Tek dosya + duz metin bilincli bir MVP tercihi: "notlarimi oku" tek bir okumayla
cevaplanabiliyor, dosya elle de duzenlenebiliyor. notes/ dizini .gitignore'da -
kisisel veri, .env ile ayni mantik.
"""

import logging
import os
from datetime import datetime

from src.jarvis.core.paths import PROJECT_ROOT
from src.jarvis.core.risk import RiskLevel
from src.jarvis.tools.base import Tool

logger = logging.getLogger("jarvis.tools.notes")

# Mutlak yol (CWD'ye bagimli DEGIL) - bkz. core/paths.py
NOTES_DIR = os.path.join(PROJECT_ROOT, "notes")
NOTES_PATH = os.path.join(NOTES_DIR, "notes.txt")

READ_NOTES_LIMIT = 5  # TTS'e okunacagi icin tum dosya degil, son N not

_CREATED_MESSAGES = {"tr": "Notunuzu kaydettim.", "en": "I've saved your note."}
_EMPTY_CONTENT_MESSAGES = {
    "tr": "Not icerigi bos, kaydetmedim.",
    "en": "The note was empty, so I didn't save it.",
}
_NO_NOTES_MESSAGES = {
    "tr": "Kayitli notunuz yok.",
    "en": "You don't have any saved notes.",
}


def _localized(messages: dict[str, str], lang: str) -> str:
    return messages.get(lang, messages["en"])


class CreateNoteTool(Tool):
    """Bir notu zaman damgasiyla notes/notes.txt'e ekler."""

    name = "create_note"
    description = "Kullanicinin soyledigi notu kalici olarak kaydeder."
    risk_level = RiskLevel.MEDIUM  # kalici dosya yazimi - onay ister

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        content = (params.get("content") or "").strip()
        if not content:
            return _localized(_EMPTY_CONTENT_MESSAGES, lang)

        os.makedirs(NOTES_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(NOTES_PATH, "a", encoding="utf-8") as notes_file:
            notes_file.write(f"[{timestamp}] {content}\n")

        logger.info("Not kaydedildi (%d karakter).", len(content))
        return _localized(_CREATED_MESSAGES, lang)


class ReadNotesTool(Tool):
    """Son birkac notu SESLI okur - salt-okunur ama KISISEL VERI, bu yuzden onay ister."""

    name = "read_notes"
    description = "Kayitli son notlari okur."
    # Salt-okunur olmasina ragmen LOW degil: guvenlik incelemesi (security-reviewer,
    # Faz 3) sunu gosterdi - listen_loop()'un FOLLOWUP penceresinde (12sn) wake-word
    # GEREKMEDEN herhangi bir ses (TV, odadaki baska biri) transkribe edilip
    # dispatcher'a dusuyor. LOW olsaydi bu, hicbir onay olmadan kullanicinin kisisel
    # notlarini hoparlorden okurdu. Risk = "eylemin geri alinabilirligi" degil,
    # "yanlis tetiklenmesinin bedeli" - burada bedel bilgi ifsasi.
    risk_level = RiskLevel.MEDIUM

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        if not os.path.isfile(NOTES_PATH):
            return _localized(_NO_NOTES_MESSAGES, lang)

        with open(NOTES_PATH, "r", encoding="utf-8") as notes_file:
            lines = [line.strip() for line in notes_file if line.strip()]

        if not lines:
            return _localized(_NO_NOTES_MESSAGES, lang)

        recent = lines[-READ_NOTES_LIMIT:]
        # Zaman damgasi TTS'te gurultu yaratiyor (her notun basinda tarih okunmasi),
        # sadece not metinlerini birlestiriyoruz - tarih dosyada duruyor.
        contents = [line.split("] ", 1)[-1] for line in recent]
        return ". ".join(contents)
