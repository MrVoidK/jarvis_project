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
    )


def _get_config() -> SecurityConfig:
    global _config_cache
    if _config_cache is None:
        _config_cache = load_security_config()
    return _config_cache


def is_path_safe(path: "os.PathLike[str] | str", config: Optional[SecurityConfig] = None) -> bool:
    """`path`, izinli dizinlerden birinin icinde mi (veya birebir kendisi mi)?

    `resolve()` symlink'leri gercek hedefe cozdugu icin bir symlink uzerinden
    izinli dizin disina kacis da otomatik yakalanir.
    """
    cfg = config or _get_config()
    resolved = Path(path).resolve(strict=False)
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
