"""CreateProjectTool + spawn_detached testleri - gerçek `claude`/subprocess YOK.

`spawn_detached` monkeypatch'lenip hangi argv/cwd ile çağrıldığı yakalanır;
`_PROJECTS_ROOT` tmp bir dizine yönlendirilir (gerçek repo'ya klasör açılmaz).

Faz 6.7 (2026-08-29). Çalıştırma: `python -m pytest tests/test_project_tool.py -v`.
"""

import os
import subprocess
from pathlib import Path

import pytest

from src.jarvis.core.risk import RiskLevel
from src.jarvis.tools import project_tool as pt
from src.jarvis.tools import subprocess_utils as su


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    """`_PROJECTS_ROOT`'u tmp'ye yönlendir, `spawn_detached`'i yakala."""
    projects = tmp_path / "jarvis_workspace" / "projects"
    monkeypatch.setattr(pt, "_PROJECTS_ROOT", projects)
    calls: list[tuple] = []
    monkeypatch.setattr(pt, "spawn_detached", lambda cmd, cwd, **kw: calls.append((cmd, cwd, kw)))
    return projects, calls


# --- CreateProjectTool ---


def test_is_high_risk_and_registered():
    from src.jarvis.tools.registry import TOOL_REGISTRY

    assert pt.CreateProjectTool.risk_level is RiskLevel.HIGH
    assert "create_project" in TOOL_REGISTRY


def test_create_project_scaffolds_claude_md_and_launches(sandbox):
    projects, calls = sandbox

    result = pt.CreateProjectTool().execute({"lang": "en", "project_name": "my-app"})

    proj = projects / "my-app"
    assert proj.is_dir()
    claude_md = (proj / "CLAUDE.md").read_text(encoding="utf-8")
    assert "my-app" in claude_md
    assert pt._TEMPLATE_PLACEHOLDER not in claude_md

    assert len(calls) == 1
    _cmd, cwd, _kw = calls[0]
    assert Path(cwd).resolve() == proj.resolve()
    assert "my-app" in result


@pytest.mark.parametrize("bad", ["../evil", "a/b", ".env", "", "   ", "-x", "a b", "foo/../bar"])
def test_create_project_rejects_unsafe_names(sandbox, bad):
    projects, calls = sandbox

    result = pt.CreateProjectTool().execute({"lang": "en", "project_name": bad})

    assert calls == []
    assert not projects.exists() or not any(projects.iterdir())
    assert "geçersiz" in result.lower() or "invalid" in result.lower()


@pytest.mark.parametrize("reserved", ["con", "CON", "nul", "com1", "lpt9", "aux.txt"])
def test_create_project_rejects_windows_reserved_names(sandbox, reserved):
    projects, calls = sandbox

    result = pt.CreateProjectTool().execute({"lang": "en", "project_name": reserved})

    assert calls == []
    assert "geçersiz" in result.lower() or "invalid" in result.lower()


def test_create_project_refuses_existing_project(sandbox):
    projects, calls = sandbox
    (projects / "dup").mkdir(parents=True)

    result = pt.CreateProjectTool().execute({"lang": "tr", "project_name": "dup"})

    assert calls == []
    assert "zaten" in result.lower()


def test_create_project_launch_failure_still_reports(sandbox, monkeypatch):
    projects, _calls = sandbox

    def _boom(cmd, cwd, **kw):
        raise RuntimeError("claude PATH'te yok")

    monkeypatch.setattr(pt, "spawn_detached", _boom)

    result = pt.CreateProjectTool().execute({"lang": "en", "project_name": "half"})

    # klasör oluştu ama başlatma başarısız - kullanıcıya net mesaj
    assert (projects / "half").is_dir()
    assert "half" in result and ("couldn't" in result.lower() or "n't start" in result.lower() or "başlat" in result.lower())


def test_create_project_missing_template_fails_soft(sandbox, monkeypatch):
    projects, calls = sandbox
    monkeypatch.setattr(pt, "_TEMPLATE_PATH", Path("Z:/definitely/missing/CLAUDE.md.template"))

    result = pt.CreateProjectTool().execute({"lang": "en", "project_name": "notmpl"})

    assert calls == []
    assert "notmpl" not in (projects / "notmpl").as_posix() or not (projects / "notmpl").exists()
    assert "wrong" in result.lower() or "hata" in result.lower()


# --- spawn_detached ---


def test_spawn_detached_scrubs_api_key(monkeypatch):
    captured: dict = {}

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs

    monkeypatch.setattr(su.subprocess, "Popen", _FakePopen)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-removed")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-should-be-removed")

    su.spawn_detached(["claude"], cwd=".")

    env = captured["kwargs"]["env"]
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    # ates-ve-unut: stdin/out/err DEVNULL, wait/communicate YOK (FakePopen'da zaten yok)
    assert captured["kwargs"]["stdin"] == subprocess.DEVNULL


def test_spawn_detached_windows_flags(monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows'a özgü creationflags")
    captured: dict = {}
    monkeypatch.setattr(su.subprocess, "Popen", lambda cmd, **kw: captured.update(kw))

    su.spawn_detached(["claude"], cwd=".")

    flags = captured["creationflags"]
    assert flags & subprocess.CREATE_NEW_PROCESS_GROUP
    assert flags & subprocess.DETACHED_PROCESS


def test_spawn_detached_wraps_launch_error(monkeypatch):
    def _raise(cmd, **kw):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(su.subprocess, "Popen", _raise)

    with pytest.raises(RuntimeError):
        su.spawn_detached(["claude"], cwd=".")
