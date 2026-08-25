"""Faz 3 arac katmani testleri - tool'lar, risk puanlama ve dispatcher kalip eslesmesi.

Dosya yazan araclar (notes, files) `monkeypatch` ile gecici bir dizine yonlendiriliyor -
testler gercek notes/ veya jarvis_workspace/ dizinine dokunmaz.

Calistirma: `python -m pytest tests/ -v` (repo kokunden, bkz. CLAUDE.md Komutlar).
"""

import os

from src.jarvis.core.dispatcher import Dispatcher
from src.jarvis.core.risk import RiskLevel, requires_approval
from src.jarvis.tools import files as files_module
from src.jarvis.tools import notes as notes_module
from src.jarvis.tools.registry import TOOL_REGISTRY
from src.jarvis.tools.shell import RunCommandTool

# --- risk puanlama ---


def test_only_low_risk_skips_approval():
    assert not requires_approval(RiskLevel.LOW)
    for level in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL):
        assert requires_approval(level), f"{level} onaysiz gecmemeli"


def test_shell_tool_is_always_high_risk():
    # Terminal komutu, icerigi ne olursa olsun ISTISNASIZ onay istemeli
    # (bkz. tools/shell.py modul docstring'i, 1. katman).
    assert RunCommandTool.risk_level is RiskLevel.HIGH


def test_registry_keys_match_tool_names():
    for name, tool in TOOL_REGISTRY.items():
        assert name == tool.name


# --- notes ---


def test_create_and_read_note(monkeypatch, tmp_path):
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr(notes_module, "NOTES_DIR", str(notes_dir))
    monkeypatch.setattr(notes_module, "NOTES_PATH", str(notes_dir / "notes.txt"))

    create = notes_module.CreateNoteTool()
    read = notes_module.ReadNotesTool()

    assert "yok" in read.execute({"lang": "tr"})  # henuz not yok

    create.execute({"lang": "tr", "content": "sut al"})
    create.execute({"lang": "tr", "content": "faturayi ode"})

    result = read.execute({"lang": "tr"})
    assert "sut al" in result
    assert "faturayi ode" in result


def test_create_note_rejects_empty_content(monkeypatch, tmp_path):
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr(notes_module, "NOTES_DIR", str(notes_dir))
    monkeypatch.setattr(notes_module, "NOTES_PATH", str(notes_dir / "notes.txt"))

    result = notes_module.CreateNoteTool().execute({"lang": "en", "content": "   "})
    assert "empty" in result.lower()
    assert not (notes_dir / "notes.txt").exists()  # dosya hic olusturulmamali


# --- files ---


def test_list_files_reports_empty_and_populated(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(files_module, "WORKSPACE_DIR", str(workspace))
    tool = files_module.ListFilesTool()

    assert "empty" in tool.execute({"lang": "en"}).lower()

    (workspace / "rapor.txt").write_text("x", encoding="utf-8")
    assert "rapor.txt" in tool.execute({"lang": "en"})


# --- shell ---


def test_run_command_executes_and_returns_output():
    result = RunCommandTool().execute({"lang": "en", "content": "echo jarvis_test_ok"})
    assert "jarvis_test_ok" in result


def test_run_command_rejects_empty_command():
    result = RunCommandTool().execute({"lang": "en", "content": ""})
    assert "didn't get a command" in result.lower()


# --- dispatcher kalip eslesmesi ---


def test_dispatcher_extracts_content_and_language():
    dispatcher = Dispatcher()

    note_tr = dispatcher.match_rule("not tut: yarin toplanti var")
    assert note_tr.name == "create_note"
    assert note_tr.parameters["lang"] == "tr"
    assert note_tr.parameters["content"] == "yarin toplanti var"

    note_en = dispatcher.match_rule("take a note: buy milk")
    assert note_en.name == "create_note"
    assert note_en.parameters["lang"] == "en"
    assert note_en.parameters["content"] == "buy milk"

    command = dispatcher.match_rule("run command: dir")
    assert command.name == "run_command"
    assert command.parameters["content"] == "dir"


def test_dispatcher_matches_contentless_intents():
    dispatcher = Dispatcher()

    for text, expected_name, expected_lang in [
        ("notlarımı oku", "read_notes", "tr"),
        ("read my notes", "read_notes", "en"),
        ("sistem durumu nedir", "get_system_info", "tr"),
        ("system status", "get_system_info", "en"),
    ]:
        intent = dispatcher.match_rule(text)
        assert intent is not None, f"eslesmedi: {text!r}"
        assert intent.name == expected_name
        assert intent.parameters["lang"] == expected_lang
        assert "content" not in intent.parameters


def test_dispatcher_returns_none_for_plain_chat():
    assert Dispatcher().match_rule("bugün hava nasıl?") is None
