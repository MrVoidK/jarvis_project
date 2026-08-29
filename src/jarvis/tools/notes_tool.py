"""Not araçları - kullanıcının Obsidian vault'undaki `Jarvis Notes/` alt ağacında.

2026-08-29 "pre-6.10 cilalama": tek sabit `Jarvis Log.md` yerine artık BAŞLIKLI
notlar (ayrı dosyalar), listeleme, tek not okuma/ekleme, Obsidian'da açma ve
birleştirme var. Eski davranış (başlıksız `create_note` / `read_notes` → günlük
log) KORUNDU (geriye dönük uyumlu).

GÜVENLİK SINIRI (eski "dosya adı ASLA LLM'den gelmez" ilkesinin BİLİNÇLİ, dar
gevşetilmesi):
- Bütün not işlemleri SADECE `<vault>/Jarvis Notes/` (+ `Jarvis Notes/Archive/`)
  içinde. Dosya adı `_note_filename(title)` ile üretilir: yalnızca harf/rakam
  (Türkçe dahil) + tire; yol ayıracı / `.` / `..` / gizli-dosya / Windows
  ayrılmış aygıt adları (CON, NUL…) elenir/öneklenir. LLM ASLA ham bir yol
  veremez, sadece bir BAŞLIK verir.
- Her işlem öncesi `is_path_safe()` + `Path.is_relative_to(_notes_dir())` ikinci
  kat kontrol. SİLME YOK - `merge_notes` kaynakları `Archive/`'e taşır (geri
  alınabilir).
"""

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from src.jarvis.core.console import print_system
from src.jarvis.core.risk import RiskLevel
from src.jarvis.core.security_config import get_obsidian_vault, is_path_safe
from src.jarvis.tools.base import Tool

logger = logging.getLogger("jarvis.tools.notes")

NOTES_SUBDIR = "Jarvis Notes"
LOG_FILENAME = "Jarvis Log.md"
ARCHIVE_SUBDIR = "Archive"

READ_NOTES_LIMIT = 5  # başlıksız log okumada son N girdi
READ_NOTES_SPOKEN_CHAR_LIMIT = 400  # sesli dönüş bu sınırda kırpılır (D1)

# Windows ayrılmış aygıt adları - `open("con.md","w")` konsola yazar (footgun).
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
# Slug: harf/rakam (Türkçe dahil, \w unicode) + boşluk + tire DIŞINDA her şeyi at.
_SLUG_DROP_RE = re.compile(r"[^\w\s-]", re.UNICODE)


def _notes_dir() -> Path:
    return Path(str(get_obsidian_vault())) / NOTES_SUBDIR


def _log_path() -> Path:
    return _notes_dir() / LOG_FILENAME


def _archive_dir() -> Path:
    return _notes_dir() / ARCHIVE_SUBDIR


def _note_filename(title: str) -> "str | None":
    """Başlıktan güvenli bir `.md` dosya adı üretir. Geçersiz/boş → `None`."""
    slug = (title or "").strip().lower()
    slug = _SLUG_DROP_RE.sub("", slug)
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:60].strip("-")
    if not slug or slug in {".", ".."}:
        return None
    if slug in _WINDOWS_RESERVED:
        slug = f"note-{slug}"
    return f"{slug}.md"


def _resolve_note_path(title: str) -> "Path | None":
    """Başlığı `Jarvis Notes/` içinde güvenli bir tam yola çözer. Güvensiz → `None`."""
    filename = _note_filename(title)
    if filename is None:
        return None
    notes_dir = _notes_dir()
    target = (notes_dir / filename).resolve()
    if not target.is_relative_to(notes_dir.resolve()) or not is_path_safe(target):
        logger.error("Not yolu güvenlik kontrolünü geçemedi: %s", target)
        return None
    return target


def _read_note_texts(path: Path, limit: int) -> list[str]:
    """Log dosyasından son `limit` girdinin metnini (zaman damgası olmadan) döner."""
    with open(path, "r", encoding="utf-8") as note_file:
        lines = [line.strip() for line in note_file if line.strip()]
    return [line.split("] ", 1)[-1] for line in lines[-limit:]]


_CREATED_MESSAGES = {"tr": "Notunuzu kaydettim.", "en": "I've saved your note."}
_CREATED_TITLED_MESSAGES = {
    "tr": "'{title}' başlıklı notu oluşturdum.",
    "en": "I've created the note titled '{title}'.",
}
_APPENDED_MESSAGES = {
    "tr": "'{title}' notuna ekledim.",
    "en": "I've added that to the note '{title}'.",
}
_EMPTY_CONTENT_MESSAGES = {
    "tr": "Not içeriği boş, kaydetmedim.",
    "en": "The note was empty, so I didn't save it.",
}
_BAD_TITLE_MESSAGES = {
    "tr": "Bu başlığı bir dosya adına çeviremedim, farklı bir başlık deneyin.",
    "en": "I couldn't turn that title into a filename, try a different one.",
}
_NO_NOTES_MESSAGES = {"tr": "Kayıtlı notunuz yok.", "en": "You don't have any saved notes."}
_NOTE_NOT_FOUND_MESSAGES = {
    "tr": "'{title}' başlığında bir not bulamadım.",
    "en": "I couldn't find a note titled '{title}'.",
}
_NOTES_TRUNCATED_SUFFIX = {
    "tr": "… (devamı Obsidian'da)",
    "en": "… (the rest is in Obsidian)",
}
_UNSAFE_PATH_MESSAGES = {
    "tr": "Not dizini güvenlik kontrolünden geçemedi, işlem iptal edildi.",
    "en": "The notes directory failed the security check, operation cancelled.",
}
_LIST_MESSAGES = {"tr": "Notlarınız: {names}.", "en": "Your notes: {names}."}
_OPENED_MESSAGES = {
    "tr": "'{title}' notunu Obsidian'da açtım.",
    "en": "I've opened the note '{title}' in Obsidian.",
}
_OPEN_FAILED_MESSAGES = {
    "tr": "'{title}' notunu açamadım (Obsidian yüklü mü?).",
    "en": "I couldn't open the note '{title}' (is Obsidian installed?).",
}
_MERGED_MESSAGES = {
    "tr": "{count} notu '{target}' altında birleştirdim, eskilerini arşive taşıdım.",
    "en": "Merged {count} notes into '{target}' and archived the originals.",
}
_MERGE_TOO_FEW_MESSAGES = {
    "tr": "Birleştirmek için en az iki mevcut not gerekiyor.",
    "en": "Merging needs at least two existing notes.",
}


def _localized(messages: dict[str, str], lang: str) -> str:
    return messages.get(lang, messages["en"])


def _ensure_notes_dir(lang: str) -> "Path | None":
    """`Jarvis Notes/`'u güvenlik kontrolünden geçirip oluşturur. Güvensiz → `None`."""
    notes_dir = _notes_dir()
    if not is_path_safe(notes_dir):
        logger.error("Not dizini güvenlik kontrolünü geçemedi: %s", notes_dir)
        return None
    os.makedirs(notes_dir, exist_ok=True)
    return notes_dir


class CreateNoteTool(Tool):
    """Not oluşturur - `title` verilirse ayrı dosya, verilmezse günlük log'a ekler."""

    name = "create_note"
    description = (
        "Not alır VEYA var olan bir nota/listeye madde ekler. Kullanıcı 'not al: "
        "...', 'X başlığıyla not al: ...', 'alışveriş listesine yumurta ekle', 'X "
        "notuna Y yaz' dediğinde bu aracı kullan. Başlık verilirse `title`, geri "
        "kalanı `content`; o başlıkta not zaten varsa ÜSTÜNE yeni madde eklenir. "
        "Başlık yoksa günlük not defterine eklenir. (Bir notu OKUMAK için "
        "read_notes, AÇMAK için open_note.)"
    )
    risk_level = RiskLevel.MEDIUM
    parameters_schema: dict = {
        "content": {"type": "string", "description": "Kaydedilecek not metni."},
        "title": {"type": "string", "description": "İsteğe bağlı not başlığı (ayrı dosya olur)."},
    }
    required_parameters: list[str] = ["content"]

    def execute(self, params: dict, stop_event=None) -> str:
        lang = params.get("lang", "en")
        content = (params.get("content") or "").strip()
        title = (params.get("title") or "").strip()
        if not content:
            return _localized(_EMPTY_CONTENT_MESSAGES, lang)

        notes_dir = _ensure_notes_dir(lang)
        if notes_dir is None:
            return _localized(_UNSAFE_PATH_MESSAGES, lang)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        if not title:
            with open(_log_path(), "a", encoding="utf-8") as log_file:
                log_file.write(f"- [{timestamp}] {content}\n")
            logger.info("Not günlüğe eklendi (%d karakter).", len(content))
            return _localized(_CREATED_MESSAGES, lang)

        target = _resolve_note_path(title)
        if target is None:
            return _localized(_BAD_TITLE_MESSAGES, lang)
        if target.exists():
            # Var olan basliga -> yeni bir madde ekle (ayri bir "append_to_note"
            # araci yok; 3B router'da not araclari birbirine karisiyordu).
            with open(target, "a", encoding="utf-8") as note_file:
                note_file.write(f"- [{timestamp}] {content}\n")
            logger.info("Mevcut nota eklendi: %s", target.name)
            return _localized(_APPENDED_MESSAGES, lang).format(title=title)
        with open(target, "w", encoding="utf-8") as note_file:
            note_file.write(f"# {title}\n\n- [{timestamp}] {content}\n")
        logger.info("Yeni başlıklı not: %s", target.name)
        return _localized(_CREATED_TITLED_MESSAGES, lang).format(title=title)


class ReadNotesTool(Tool):
    """Bir notu SESLİ okur - `title` verilirse o notu, verilmezse günlükten son birkaçını."""

    name = "read_notes"
    description = (
        "Kayıtlı bir notu sesli okur. Kullanıcı belirli bir BAŞLIK söylerse "
        "('alışveriş listesi notunu oku') onu `title` olarak geçir; başlık yoksa "
        "günlük not defterindeki son notlar okunur."
    )
    risk_level = RiskLevel.MEDIUM  # kişisel veri - bkz. eski docstring gerekçesi
    parameters_schema: dict = {
        "title": {"type": "string", "description": "İsteğe bağlı: okunacak notun başlığı."}
    }
    required_parameters: list[str] = []

    def execute(self, params: dict, stop_event=None) -> str:
        lang = params.get("lang", "en")
        title = (params.get("title") or "").strip()

        if not is_path_safe(_notes_dir()):
            logger.error("Not dizini güvenlik kontrolünü geçemedi.")
            return _localized(_UNSAFE_PATH_MESSAGES, lang)

        if title:
            target = _resolve_note_path(title)
            if target is None:
                return _localized(_BAD_TITLE_MESSAGES, lang)
            if not target.is_file():
                return _localized(_NOTE_NOT_FOUND_MESSAGES, lang).format(title=title)
            body = target.read_text(encoding="utf-8").strip()
        else:
            if not _log_path().is_file():
                return _localized(_NO_NOTES_MESSAGES, lang)
            contents = _read_note_texts(_log_path(), READ_NOTES_LIMIT)
            if not contents:
                return _localized(_NO_NOTES_MESSAGES, lang)
            body = ". ".join(contents)

        if len(body) > READ_NOTES_SPOKEN_CHAR_LIMIT:
            print_system(f"Not içeriği:\n{body}", level="info")
            clipped = body[:READ_NOTES_SPOKEN_CHAR_LIMIT].rsplit(" ", 1)[0]
            return f"{clipped}{_localized(_NOTES_TRUNCATED_SUFFIX, lang)}"
        return body


class ListNotesTool(Tool):
    """`Jarvis Notes/` içindeki başlıklı notların adlarını listeler."""

    name = "list_notes"
    description = (
        "Kayıtlı başlıklı notların adlarını söyler. Kullanıcı 'notlarımı listele', "
        "'hangi notlarım var', 'list my notes' dediğinde kullan."
    )
    risk_level = RiskLevel.MEDIUM
    parameters_schema: dict = {}
    required_parameters: list[str] = []

    def execute(self, params: dict, stop_event=None) -> str:
        lang = params.get("lang", "en")
        notes_dir = _notes_dir()
        if not is_path_safe(notes_dir):
            return _localized(_UNSAFE_PATH_MESSAGES, lang)
        if not notes_dir.is_dir():
            return _localized(_NO_NOTES_MESSAGES, lang)

        names = sorted(
            p.stem
            for p in notes_dir.glob("*.md")
            if p.is_file() and p.name != LOG_FILENAME
        )
        if not names:
            return _localized(_NO_NOTES_MESSAGES, lang)
        return _localized(_LIST_MESSAGES, lang).format(names=", ".join(names))


class OpenNoteTool(Tool):
    """Bir başlıklı notu Obsidian'da açar (`obsidian://open` URI)."""

    name = "open_note"
    description = (
        "Belirli bir başlıklı notu Obsidian uygulamasında açar. Kullanıcı "
        "'alışveriş listesi notunu aç', 'open my X note in Obsidian' dediğinde kullan."
    )
    risk_level = RiskLevel.MEDIUM
    parameters_schema: dict = {
        "title": {"type": "string", "description": "Açılacak notun başlığı."}
    }
    required_parameters: list[str] = ["title"]

    def execute(self, params: dict, stop_event=None) -> str:
        lang = params.get("lang", "en")
        title = (params.get("title") or "").strip()
        target = _resolve_note_path(title)
        if target is None:
            return _localized(_BAD_TITLE_MESSAGES, lang)
        if not target.is_file():
            return _localized(_NOTE_NOT_FOUND_MESSAGES, lang).format(title=title)

        vault_name = Path(str(get_obsidian_vault())).name
        rel = f"{NOTES_SUBDIR}/{target.stem}"
        uri = f"obsidian://open?vault={quote(vault_name)}&file={quote(rel)}"
        try:
            os.startfile(uri)  # noqa: S606 - obsidian:// URI şeması, shell yorumu yok
        except OSError:
            logger.warning("Obsidian URI açılamadı: %r", uri)
            return _localized(_OPEN_FAILED_MESSAGES, lang).format(title=title)
        logger.info("Obsidian'da açıldı: %s", uri)
        return _localized(_OPENED_MESSAGES, lang).format(title=title)


class MergeNotesTool(Tool):
    """Birden fazla başlıklı notu tek bir hedef notta birleştirir; kaynakları arşive taşır."""

    name = "merge_notes"
    description = (
        "İki veya daha fazla mevcut notu tek bir notta birleştirir. Kullanıcı "
        "'A ve B notlarını birleştir', 'merge my X and Y notes into Z' dediğinde "
        "kullan. `sources` = birleştirilecek not başlıkları (virgülle ayrılmış), "
        "`target` = sonuç notunun başlığı. Kaynak notlar SİLİNMEZ, arşive taşınır."
    )
    risk_level = RiskLevel.MEDIUM
    parameters_schema: dict = {
        "sources": {"type": "string", "description": "Birleştirilecek not başlıkları, virgülle ayrılmış."},
        "target": {"type": "string", "description": "Sonuç notunun başlığı."},
    }
    required_parameters: list[str] = ["sources", "target"]

    def execute(self, params: dict, stop_event=None) -> str:
        lang = params.get("lang", "en")
        raw_sources = params.get("sources")
        source_titles = (
            [str(s).strip() for s in raw_sources]
            if isinstance(raw_sources, list)
            else [s.strip() for s in str(raw_sources or "").split(",")]
        )
        source_titles = [t for t in source_titles if t]
        target_title = (params.get("target") or "").strip()

        notes_dir = _ensure_notes_dir(lang)
        if notes_dir is None:
            return _localized(_UNSAFE_PATH_MESSAGES, lang)
        target = _resolve_note_path(target_title)
        if target is None:
            return _localized(_BAD_TITLE_MESSAGES, lang)

        resolved_sources: list[Path] = []
        for title in source_titles:
            src = _resolve_note_path(title)
            if src is not None and src.is_file() and src != target:
                resolved_sources.append(src)
        if len(resolved_sources) < 2:
            return _localized(_MERGE_TOO_FEW_MESSAGES, lang)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        blocks = [f"# {target_title}\n\n_{timestamp} — birleştirildi_\n"]
        for src in resolved_sources:
            blocks.append(f"\n\n---\n\n## {src.stem}\n\n{src.read_text(encoding='utf-8').strip()}\n")
        existing = target.read_text(encoding="utf-8").strip() + "\n" if target.exists() else ""
        target.write_text(existing + "".join(blocks), encoding="utf-8")

        archive = _archive_dir()
        os.makedirs(archive, exist_ok=True)
        for src in resolved_sources:
            dest = archive / src.name
            if dest.exists():
                dest = archive / f"{src.stem}-{timestamp.replace(':', '').replace(' ', '-')}.md"
            src.rename(dest)
        logger.info("%d not '%s' altında birleştirildi.", len(resolved_sources), target.name)
        return _localized(_MERGED_MESSAGES, lang).format(
            count=len(resolved_sources), target=target_title
        )
