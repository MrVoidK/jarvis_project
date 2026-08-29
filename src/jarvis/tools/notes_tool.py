"""Not alma araclari - artik kullanicinin gercek Obsidian vault'una yaziyor.

Eskiden PROJECT_ROOT/notes/notes.txt'e yaziyordu (tools/notes.py); artik
config/security.yaml'daki allowed_directories icinden Obsidian vault yolunu
okuyup, vault icinde SABIT, kod-tanimli tek bir dosyaya
(`<vault>/Jarvis Notes/Jarvis Log.md`) zaman damgali Markdown liste ogeleri
olarak yaziyor/okuyor.

BILINCLI KAPSAM SINIRLAMASI: dosya adi ASLA LLM parametresinden gelmez -
sadece not ICERIGI (`content`) gelir. Vault'un tamamina, LLM'in sectigi
KEYFI bir dosya adiyla yazma/okuma izni AŞIRI GENIS bir saldiri yuzeyi
acardi (halüsinasyon sonucu vault'taki mevcut bir notun uzerine yazilmasi/
silinmesi riski). Not-basina-dosya istenirse bu, ayri ve daha dikkatli bir
guvenlik incelemesi gerektiren bir gelecek adimdir.

Yazma/okuma ONCESI is_path_safe() ikinci bir savunma katmani olarak
cagrilir - VAULT_ROOT kod-sabit olsa bile, security.yaml yanlis
yapilandirilirsa (orn. vault yolu degisirse) sessizce yanlis yere yazmayi
engeller.
"""

import logging
import os
from datetime import datetime

from src.jarvis.core.console import print_system
from src.jarvis.core.risk import RiskLevel
from src.jarvis.core.security_config import get_obsidian_vault, is_path_safe
from src.jarvis.tools.base import Tool

logger = logging.getLogger("jarvis.tools.notes")

NOTES_SUBDIR = "Jarvis Notes"
LOG_FILENAME = "Jarvis Log.md"

READ_NOTES_LIMIT = 5  # TTS'e okunacagi icin tum dosya degil, son N not
READ_NOTES_SPOKEN_CHAR_LIMIT = 400  # D1 (2026-08-29): 5 uzun not = uzun TTS.
                                     # Sesli donus bu sinirda kirpilir + "devami
                                     # Obsidian'da"; tam metin print_system ile.


def _notes_dir() -> str:
    return os.path.join(str(get_obsidian_vault()), NOTES_SUBDIR)


def _notes_path() -> str:
    return os.path.join(_notes_dir(), LOG_FILENAME)


_CREATED_MESSAGES = {"tr": "Notunuzu kaydettim.", "en": "I've saved your note."}
_EMPTY_CONTENT_MESSAGES = {
    "tr": "Not icerigi bos, kaydetmedim.",
    "en": "The note was empty, so I didn't save it.",
}
_NO_NOTES_MESSAGES = {
    "tr": "Kayitli notunuz yok.",
    "en": "You don't have any saved notes.",
}
_NOTES_TRUNCATED_SUFFIX = {
    "tr": "… (devamı Obsidian'da)",
    "en": "… (the rest is in Obsidian)",
}
_UNSAFE_PATH_MESSAGES = {
    "tr": "Not dizini güvenlik kontrolünden geçemedi, işlem iptal edildi.",
    "en": "The notes directory failed the security check, operation cancelled.",
}


def _localized(messages: dict[str, str], lang: str) -> str:
    return messages.get(lang, messages["en"])


class CreateNoteTool(Tool):
    """Bir notu zaman damgasiyla Obsidian vault'undaki Jarvis Log.md'ye ekler."""

    name = "create_note"
    description = "Kullanicinin soyledigi notu Obsidian vault'una kalici olarak kaydeder."
    risk_level = RiskLevel.MEDIUM  # kalici dosya yazimi - onay ister
    parameters_schema: dict = {
        "content": {"type": "string", "description": "Kaydedilecek not metni."}
    }
    required_parameters: list[str] = ["content"]

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        content = (params.get("content") or "").strip()
        if not content:
            return _localized(_EMPTY_CONTENT_MESSAGES, lang)

        notes_dir = _notes_dir()
        notes_path = _notes_path()
        if not is_path_safe(notes_dir):
            logger.error("Not dizini guvenlik kontrolunu gecemedi: %s", notes_dir)
            return _localized(_UNSAFE_PATH_MESSAGES, lang)

        os.makedirs(notes_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(notes_path, "a", encoding="utf-8") as notes_file:
            notes_file.write(f"- [{timestamp}] {content}\n")

        logger.info("Not kaydedildi (%d karakter).", len(content))
        return _localized(_CREATED_MESSAGES, lang)


class ReadNotesTool(Tool):
    """Son birkac notu SESLI okur - salt-okunur ama KISISEL VERI, bu yuzden onay ister."""

    name = "read_notes"
    description = "Obsidian vault'undaki kayitli son notlari okur."
    # Salt-okunur olmasina ragmen LOW degil: listen_loop()'un FOLLOWUP penceresinde
    # (12sn) wake-word GEREKMEDEN herhangi bir ses transkribe edilip dispatcher'a
    # dusebiliyor. LOW olsaydi bu, hicbir onay olmadan kullanicinin kisisel
    # notlarini hoparlorden okurdu. Risk = "eylemin geri alinabilirligi" degil,
    # "yanlis tetiklenmesinin bedeli" - burada bedel bilgi ifsasi.
    risk_level = RiskLevel.MEDIUM
    parameters_schema: dict = {}
    required_parameters: list[str] = []

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        notes_path = _notes_path()

        if not is_path_safe(notes_path):
            logger.error("Not dizini guvenlik kontrolunu gecemedi: %s", notes_path)
            return _localized(_UNSAFE_PATH_MESSAGES, lang)

        if not os.path.isfile(notes_path):
            return _localized(_NO_NOTES_MESSAGES, lang)

        with open(notes_path, "r", encoding="utf-8") as notes_file:
            lines = [line.strip() for line in notes_file if line.strip()]

        if not lines:
            return _localized(_NO_NOTES_MESSAGES, lang)

        recent = lines[-READ_NOTES_LIMIT:]
        # Zaman damgasi TTS'te gurultu yaratiyor (her notun basinda tarih okunmasi),
        # sadece not metinlerini birlestiriyoruz - tarih dosyada duruyor.
        contents = [line.split("] ", 1)[-1] for line in recent]
        joined = ". ".join(contents)

        # D1 (2026-08-29): cok uzun notlar tamamen sesli okunmasin - tam metni
        # konsola/HUD'a bas, sesli donusu kelime siniriyla kirp.
        if len(joined) > READ_NOTES_SPOKEN_CHAR_LIMIT:
            print_system(f"Son {len(recent)} not:\n{joined}", level="info")
            clipped = joined[:READ_NOTES_SPOKEN_CHAR_LIMIT].rsplit(" ", 1)[0]
            return f"{clipped}{_localized(_NOTES_TRUNCATED_SUFFIX, lang)}"
        return joined
