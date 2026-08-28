"""Zero-Trust dosya/uygulama izin listesi - config/security.yaml'dan okunur.

Faz 3 "computer-use" araclari (notes_tool, terminal_tool) artik PROJECT_ROOT
disina (kullanicinin Obsidian vault'u gibi kisisel, makineye ozel yollara)
yaziyor/okuyor. Bu izinleri kodun icine gommek yerine (her makinede farkli
olacagi icin) ayri bir YAML dosyasindan okuyoruz - `security.yaml` kisisel
yol icerdigi icin .gitignore'da, commit'lenen `security.example.yaml` sablon
olarak duruyor (bkz. tools/notes.py'nin eski NOTES_DIR/notes/ .gitignore
mantigiyla ayni ilke: kisisel veri asla repoya girmez).

`is_path_safe()`, path traversal (`../../`) ve symlink-kacisini `Path.resolve()`
ile normalize edip `Path.is_relative_to()` karsilastirmasi kullanarak engeller -
string-prefix karsilastirmasi BILINCLI OLARAK kullanilmiyor: "C:\\vault2"
gibi bir kardes dizin, "C:\\vault" icin prefix-string testiyle yanlislikla
"icinde" sayilirdi.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from src.jarvis.core.paths import PROJECT_ROOT

logger = logging.getLogger("jarvis.core.security_config")

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "security.yaml")
EXAMPLE_CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "security.example.yaml")


@dataclass
class SecurityConfig:
    allowed_directories: list[Path] = field(default_factory=list)
    known_applications: dict[str, str] = field(default_factory=dict)
    obsidian_vault: Optional[Path] = None
    # agents/registry/*.yaml dinamik arac manifest'leri icin allowlist (Faz 6.4).
    # Bir manifest yalnizca dosya kokunun (stem) burada da bulunmasi halinde
    # yuklenir - bkz. core/registry_loader.py:load_dynamic_tools().
    enabled_dynamic_agents: list[str] = field(default_factory=list)


_config_cache: Optional[SecurityConfig] = None


def _resolve_directory(raw: str) -> Path:
    """Goreli yollari PROJECT_ROOT'a gore cozer, mutlak yollari oldugu gibi birakir."""
    path = Path(raw)
    base = path if path.is_absolute() else Path(PROJECT_ROOT) / path
    return base.resolve(strict=False)


def load_security_config(path: str = CONFIG_PATH) -> SecurityConfig:
    """`config/security.yaml`'i okuyup SecurityConfig'e cevirir.

    Dosya yoksa (kullanici henuz security.example.yaml'i kopyalamadiysa) net
    bir hata firlatilir - sessizce bos bir config'e dusup is_path_safe()'in
    her seyi reddetmesi (guvenli ama kafa karistirici) yerine, kurulum
    adiminin eksik oldugu acikca soyleniyor.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"'{path}' bulunamadi. '{EXAMPLE_CONFIG_PATH}' dosyasini "
            f"'{path}' olarak kopyalayip kendi yollarinizi girin."
        )

    with open(path, "r", encoding="utf-8") as config_file:
        raw = yaml.safe_load(config_file) or {}

    allowed_directories = [
        _resolve_directory(entry) for entry in raw.get("allowed_directories", [])
    ]
    known_applications = {
        str(name).strip().lower(): str(command)
        for name, command in (raw.get("known_applications") or {}).items()
    }
    enabled_dynamic_agents = [
        str(entry).strip()
        for entry in (raw.get("enabled_dynamic_agents") or [])
        if str(entry).strip()
    ]

    obsidian_vault: Optional[Path] = None
    raw_vault = raw.get("obsidian_vault")
    if raw_vault:
        obsidian_vault = _resolve_directory(raw_vault)
        # notes_tool.py'nin is_path_safe() kontrolunden gecebilmesi icin vault
        # otomatik olarak izinli dizinler listesine de eklenir - security.yaml'da
        # ayni yolu iki kez yazma zorunlulugu olmasin diye.
        if obsidian_vault not in allowed_directories:
            allowed_directories.append(obsidian_vault)

    return SecurityConfig(
        allowed_directories=allowed_directories,
        known_applications=known_applications,
        obsidian_vault=obsidian_vault,
        enabled_dynamic_agents=enabled_dynamic_agents,
    )


def _get_config() -> SecurityConfig:
    global _config_cache
    if _config_cache is None:
        _config_cache = load_security_config()
    return _config_cache


# Windows aygit ad-alani (`\\?\`, `\\.\`) ve UNC (`\\server\share`) onekleri.
# Path.resolve() bunlari guvenilir sekilde normalize ETMEZ; boyle bir yol
# is_relative_to() karsilastirmasinda beklenmedik davranabilir veya bir aga
# paylasimini yanlislikla "izinli dizin icinde" gibi gosterebilir. Bir tool
# LLM/kullanici turevli bir yol parametresini buraya gecirmeye basladiginda
# (Faz 6.7 CreateProjectTool) bu onekler dogrudan reddedilmeli.
_DEVICE_NAMESPACE_PREFIXES = ("\\\\?\\", "\\\\.\\", "//?/", "//./")

# LLM/kullanici turevli TEK bir yol bileseni icin izin listesi: harf-rakam ile
# baslar, govdede yalnizca nokta/tire/alt-tire. Bosluk, yol ayiraci, gizli-dosya
# onegi, "." / "..", surucu/ADS (`:`) ve kontrol karakterleri disarida kalir.
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _has_unsafe_prefix(raw: "os.PathLike[str] | str") -> bool:
    """`raw`, UNC veya Windows aygit ad-alani onekiyle mi basliyor? (bkz. yorum)"""
    s = str(raw).strip()
    return s.startswith(_DEVICE_NAMESPACE_PREFIXES) or s.startswith(("\\\\", "//"))


def is_safe_component_name(name: str) -> bool:
    """LLM/kullanici turevli TEK bir yol bileseni (proje/klasor/dosya adi) guvenli mi?

    Harf-rakam ile baslar; govdede `.` `-` `_` serbest. Yasak: bosluk, yol
    ayiraci (`/` `\\`), `.` / `..`, gizli-dosya onegi, `:` (surucu/ADS),
    kontrol karakteri, bos string. `is_path_safe()` tam yolu izinli dizinlere
    KARSI dogrular; bu ise henuz path'e cevrilmemis, guvenilmeyen tek parcayi
    erken eler (defense-in-depth - CreateProjectTool project_name'i bununla
    suzup sonra is_path_safe'e gecirir).
    """
    return (
        bool(name)
        and name not in {".", ".."}
        and _SAFE_COMPONENT_RE.fullmatch(name) is not None
    )


def is_path_safe(
    path: "os.PathLike[str] | str",
    config: Optional[SecurityConfig] = None,
    *,
    allow_create: bool = True,
) -> bool:
    """`path`, izinli dizinlerden birinin icinde mi (veya birebir kendisi mi)?

    UNC / aygit ad-alani onekli yollar (`\\\\server\\share`, `\\\\?\\...`,
    `\\\\.\\...`) her zaman dogrudan reddedilir - resolve() bunlari guvenilir
    normalize etmedigi icin containment kontrolu yanildabilir.

    `allow_create`: varsayilan `True` iken yalnizca containment bakilir (yolun
    diskte var olmasi gerekmez - notes_tool ilk not oncesi henuz olmayan bir
    dizini kontrol ediyor). `False` verilirse yol ayrica diskte VAR olmali;
    LLM'in urettigi bir yolu alan, "yazim hatasiyla yeni dosya olusturma"
    istemeyen okuma araclari icin. (v2 §7.2 varsayilani `False` idi; mevcut
    cagiranlari kirmamak icin burada `True`'ya cevrildi - bilincli sapma.)

    Tek bir dosya/klasor adinin (LLM turevli) karakter allowlist'i icin ayri
    `is_safe_component_name()` var - bu fonksiyon dizin-bazli calisir, izinli
    bir dizin icindeki `.env` gibi hassas dosyalara erisimi engellemez (kabul
    edilen sinir).

    `resolve()` symlink'leri gercek hedefe cozdugu icin bir symlink uzerinden
    izinli dizin disina kacis da otomatik yakalanir.
    """
    if _has_unsafe_prefix(path):
        logger.warning("Path guvensiz onek (UNC/aygit ad-alani), reddedildi: %s", path)
        return False

    cfg = config or _get_config()
    resolved = Path(path).resolve(strict=False)

    if not allow_create and not resolved.exists():
        logger.warning("Path yok (allow_create=False), reddedildi: %s", resolved)
        return False

    for allowed in cfg.allowed_directories:
        if resolved == allowed or resolved.is_relative_to(allowed):
            return True
    logger.warning("Path guvenlik kontrolunu gecemedi: %s", resolved)
    return False


def resolve_app_command(app_name: str, config: Optional[SecurityConfig] = None) -> Optional[str]:
    """`known_applications` allowlist'inde (case-insensitive) bir eslesme arar."""
    cfg = config or _get_config()
    return cfg.known_applications.get(app_name.strip().lower())


def get_obsidian_vault(config: Optional[SecurityConfig] = None) -> Path:
    """Obsidian vault yolunu dondurur; security.yaml'da tanimli degilse hata verir."""
    cfg = config or _get_config()
    if cfg.obsidian_vault is None:
        raise RuntimeError(
            "config/security.yaml'da 'obsidian_vault' tanimli degil - "
            "vault yolunuzu security.yaml'a ekleyin (bkz. security.example.yaml)."
        )
    return cfg.obsidian_vault
