"""CreateProjectTool - jarvis_workspace altında yeni bir proje iskeleti kurar ve
o dizinde YENİ BİR TERMİNAL PENCERESİNDE interaktif Claude Code başlatır.

Faz 6.7 (v2 §6). Bağımlı: Faz 6.1'in sertleştirilmiş `is_safe_component_name()` +
`_has_unsafe_prefix()` (LLM türevli `project_name` → yol; bu, `project_name`'in
bir dosya yoluna dönüştüğü İLK yer). `RunCommandTool`'un `communicate(timeout=15)`
modeli yerine `subprocess_utils.spawn_detached()` (ateşle-ve-unut) - bir Claude
Code oturumu ana thread'i bloklayamaz.

GÜVENLİK:
- `risk_level = HIGH` → istisnasız `[Y/N]` onayı (`core/risk.py:requires_approval`).
- `project_name` `is_safe_component_name()` allowlist'inden geçer (harf-rakam
  başlar; gövdede yalnızca `. - _`) + Windows ayrılmış aygıt adları reddedilir +
  hedef yol `_PROJECTS_ROOT` altında olmalı (belt-and-suspenders containment).
- Var olan bir proje ADI üzerine YAZILMAZ.
- `claude` süreci ASLA API key ile başlatılmaz (`spawn_detached` env'i temizler);
  giriş gerekirse kullanıcı açılan terminal penceresinden yapar.
"""

import logging
import os
from pathlib import Path

from src.jarvis.core.paths import PROJECT_ROOT
from src.jarvis.core.risk import RiskLevel
from src.jarvis.core.security_config import _has_unsafe_prefix, is_safe_component_name
from src.jarvis.tools.base import Tool
from src.jarvis.tools.subprocess_utils import spawn_detached

logger = logging.getLogger("jarvis.tools.project")

# JARVIS-içi, kod-sabit konum (kullanıcı-yapılandırmasız) - `notes_tool`'un
# kod-sabit vault deseni gibi. `security.yaml:allowed_directories`'e EKLENMEZ.
_PROJECTS_ROOT = Path(PROJECT_ROOT) / "jarvis_workspace" / "projects"
_TEMPLATE_PATH = Path(PROJECT_ROOT) / "templates" / "CLAUDE.md.template"
_TEMPLATE_PLACEHOLDER = "{{PROJECT_NAME}}"

# Windows ayrılmış aygıt adları - `is_safe_component_name()` bunları geçirir
# ("con" alfanümerik) ama `mkdir con` Windows'ta patolojik davranır.
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

_INVALID_NAME_MESSAGES = {
    "tr": "Geçersiz proje adı. Harf veya rakamla başlamalı; sadece nokta, tire, alt çizgi kullanılabilir.",
    "en": "Invalid project name. It must start with a letter or digit and use only dots, hyphens, underscores.",
}
_EXISTS_MESSAGES = {
    "tr": "'{name}' adında bir proje zaten var, üzerine yazmadım.",
    "en": "A project called '{name}' already exists, I didn't overwrite it.",
}
_CREATED_MESSAGES = {
    "tr": "'{name}' projesi oluşturuldu, Claude Code yeni bir pencerede başlatıldı. Giriş istenirse o pencereden yapın.",
    "en": "Project '{name}' created and Claude Code launched in a new window. Sign in there if prompted.",
}
_LAUNCH_FAILED_MESSAGES = {
    "tr": "'{name}' projesi oluşturuldu ama Claude Code'u başlatamadım ('claude' PATH'te mi?).",
    "en": "Project '{name}' was created but I couldn't start Claude Code (is 'claude' on PATH?).",
}
_TOOL_FAILED_MESSAGES = {
    "tr": "Projeyi oluştururken bir hata oluştu.",
    "en": "Something went wrong while creating the project.",
}


def _localized(messages: dict[str, str], lang: str) -> str:
    return messages.get(lang, messages["en"])


def _launch_claude_code(cwd: str) -> None:
    """Yeni bir terminal penceresinde, `cwd` dizininde interaktif `claude`
    başlatır. Windows: `start "" cmd /k "cd /d <cwd> && claude"` - `cmd /k`
    pencereyi AÇIK tutar (giriş istemi orada görünür). Diğer OS: doğrudan
    `claude` (fallback, kendi başlatıldığı terminali kullanır).

    `cwd` çağıran tarafından doğrulanmış (`_PROJECTS_ROOT` + `is_safe_component_name`)
    olduğu için string/`shell=True` yolu burada enjeksiyon yüzeyi açmaz."""
    if os.name == "nt":
        spawn_detached(f'start "" cmd /k "cd /d "{cwd}" && claude"', cwd=cwd, new_console=True)
    else:
        spawn_detached(["claude"], cwd=cwd)


class CreateProjectTool(Tool):
    name = "create_project"
    description = (
        "Yeni bir yazılım projesi başlatır: jarvis_workspace altında bir klasör "
        "oluşturur, CLAUDE.md iskeleti koyar ve o dizinde yeni bir terminal "
        "penceresinde Claude Code'u açar. Kullanıcı 'yeni proje oluştur', "
        "'X adında bir proje başlat', 'create a project called X' dediğinde kullan."
    )
    risk_level = RiskLevel.HIGH  # dosya sistemi + subprocess + dış CLI
    parameters_schema: dict = {
        "project_name": {
            "type": "string",
            "description": "Yeni projenin adı (harf/rakamla başlar; nokta, tire, alt çizgi serbest).",
        }
    }
    required_parameters: list[str] = ["project_name"]

    def execute(self, params: dict, stop_event=None) -> str:
        lang = params.get("lang", "en")
        name = (params.get("project_name") or "").strip()

        if (
            not is_safe_component_name(name)
            or _has_unsafe_prefix(name)
            or name.split(".")[0].lower() in _WINDOWS_RESERVED
        ):
            logger.warning("create_project: geçersiz proje adı reddedildi: %r", name)
            return _localized(_INVALID_NAME_MESSAGES, lang)

        projects_root = _PROJECTS_ROOT.resolve()
        target = (_PROJECTS_ROOT / name).resolve()
        # is_safe_component_name zaten `/`, `\`, `..` bloklar - bu, ikinci kat.
        if target != projects_root and not target.is_relative_to(projects_root):
            logger.error("create_project: hedef projects_root dışında: %s", target)
            return _localized(_INVALID_NAME_MESSAGES, lang)

        if target.exists():
            return _localized(_EXISTS_MESSAGES, lang).format(name=name)

        try:
            template = _TEMPLATE_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("create_project: template okunamadı (%s): %s", _TEMPLATE_PATH, exc)
            return _localized(_TOOL_FAILED_MESSAGES, lang)

        target.mkdir(parents=True)
        (target / "CLAUDE.md").write_text(
            template.replace(_TEMPLATE_PLACEHOLDER, name), encoding="utf-8"
        )
        logger.info("create_project: %s oluşturuldu.", target)

        try:
            _launch_claude_code(str(target))
        except RuntimeError as exc:
            logger.error("create_project: Claude Code başlatılamadı: %s", exc)
            return _localized(_LAUNCH_FAILED_MESSAGES, lang).format(name=name)

        return _localized(_CREATED_MESSAGES, lang).format(name=name)
