"""security_config.py testleri - allowed_directories path-traversal/symlink korumasi.

Calistirma: `python -m pytest tests/ -v` (repo kokunden, bkz. CLAUDE.md Komutlar).
"""

import sys

import pytest

from src.jarvis.core import security_config as security_config_module
from src.jarvis.core.security_config import (
    SecurityConfig,
    is_path_safe,
    load_security_config,
    resolve_app_command,
)


def _config_for(tmp_path):
    allowed = tmp_path / "vault"
    allowed.mkdir()
    return SecurityConfig(allowed_directories=[allowed.resolve()], known_applications={"vs code": "code"})


def test_is_path_safe_accepts_directory_itself(tmp_path):
    config = _config_for(tmp_path)
    assert is_path_safe(config.allowed_directories[0], config=config)


def test_is_path_safe_accepts_file_inside_allowed_directory(tmp_path):
    config = _config_for(tmp_path)
    inner_file = config.allowed_directories[0] / "note.md"
    assert is_path_safe(inner_file, config=config)


def test_is_path_safe_rejects_traversal_outside_allowed_directory(tmp_path):
    config = _config_for(tmp_path)
    traversal = config.allowed_directories[0] / ".." / ".." / "etc" / "passwd"
    assert not is_path_safe(traversal, config=config)


def test_is_path_safe_rejects_unrelated_sibling_directory(tmp_path):
    # "vault2", "vault" icin string-prefix testiyle yanlislikla "icinde" sayilirdi -
    # Path.is_relative_to() bunu dogru sekilde reddetmeli.
    config = _config_for(tmp_path)
    sibling = tmp_path / "vault2" / "note.md"
    assert not is_path_safe(sibling, config=config)


@pytest.mark.skipif(sys.platform == "win32", reason="os.symlink genelde yonetici izni gerektirir")
def test_is_path_safe_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("x", encoding="utf-8")

    config = _config_for(tmp_path)
    link = config.allowed_directories[0] / "escape_link"
    link.symlink_to(secret)

    assert not is_path_safe(link, config=config)


def test_load_security_config_happy_path(tmp_path, monkeypatch):
    vault = tmp_path / "MyVault"
    vault.mkdir()
    yaml_path = tmp_path / "security.yaml"
    yaml_path.write_text(
        f"""
allowed_directories:
  - "jarvis_workspace"
  - "{str(vault).replace(chr(92), chr(92) * 2)}"
known_applications:
  "vs code": "code"
""",
        encoding="utf-8",
    )

    config = load_security_config(str(yaml_path))

    assert vault.resolve() in config.allowed_directories
    assert config.known_applications["vs code"] == "code"


def test_load_security_config_missing_file_raises(tmp_path):
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(FileNotFoundError):
        load_security_config(str(missing))


def test_resolve_app_command_is_case_insensitive(tmp_path):
    config = _config_for(tmp_path)
    assert resolve_app_command("VS Code", config=config) == "code"
    assert resolve_app_command("unknown-app", config=config) is None
