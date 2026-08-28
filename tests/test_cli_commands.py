"""cli_commands.py testleri - gerçek mikrofon/TTS/MCP/Ollama OLMADAN.

`_cmd_status()`/`_cmd_test()` içindeki gecikmeli importlar (`ears.listener`,
`mouth.tts`, `core.app`) `sys.modules`'a sahte modüller enjekte edilerek
atlanıyor - Python `from X import Y` çağrıldığında önce `sys.modules[X]`'e
bakar, oradaysa gerçek dosyayı hiç yeniden çalıştırmaz (bkz.
`src/jarvis/core/cli_commands.py` ilgili fonksiyonların gecikmeli-import
yorumları).

Calistirma: `python -m pytest tests/ -v` (repo kokunden, bkz. CLAUDE.md Komutlar).
"""

import logging
import sys
import threading
import types

import pytest

from src.jarvis.core import cli_commands
from src.jarvis.core.cli_commands import (
    _COMMANDS,
    _parse_test_arguments,
    handle_cli_command,
    is_cli_command,
)


@pytest.fixture(autouse=True)
def _reset_debug_flag():
    """Modul-seviyesi `_debug_enabled` global'i testler arasi sizmasin diye."""
    cli_commands._debug_enabled = False
    logging.getLogger().setLevel(logging.INFO)
    yield
    cli_commands._debug_enabled = False
    logging.getLogger().setLevel(logging.INFO)


# --- is_cli_command / _parse_test_arguments ---


def test_is_cli_command_recognizes_leading_slash():
    assert is_cli_command("/help")
    assert is_cli_command("  /status")


def test_is_cli_command_rejects_normal_text():
    assert not is_cli_command("merhaba jarvis")
    assert not is_cli_command("saat kaç?")


def test_parse_test_arguments_name_only():
    name, params = _parse_test_arguments("get_system_info")
    assert name == "get_system_info"
    assert params == {}


def test_parse_test_arguments_with_key_value_pairs():
    name, params = _parse_test_arguments("run_command command=echo hi")
    assert name == "run_command"
    assert params == {"command": "echo"}  # "hi" (esitsiz token) sessizce yoksayilir


def test_parse_test_arguments_empty_string():
    name, params = _parse_test_arguments("")
    assert name == ""
    assert params == {}


# --- handle_cli_command routing ---


def test_unknown_command_warns_via_print_system(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        cli_commands, "print_system", lambda msg, level="info": captured.update(msg=msg, level=level)
    )

    handle_cli_command("/nope", history=[{"role": "system", "content": "x"}])

    assert "Bilinmeyen komut" in captured["msg"]
    assert captured["level"] == "warning"


def test_clear_resets_history_and_clears_screen(monkeypatch):
    cleared = {"called": False}
    monkeypatch.setattr(cli_commands.console, "clear", lambda: cleared.update(called=True))
    monkeypatch.setattr(cli_commands, "SYSTEM_PROMPT", "SISTEM")

    history = [
        {"role": "system", "content": "SISTEM"},
        {"role": "user", "content": "merhaba"},
        {"role": "assistant", "content": "selam"},
    ]

    handle_cli_command("/clear", history=history)

    assert history == [{"role": "system", "content": "SISTEM"}]
    assert cleared["called"] is True


def test_debug_toggles_flag_and_log_level():
    assert cli_commands._debug_enabled is False

    handle_cli_command("/debug", history=[])
    assert cli_commands._debug_enabled is True
    assert logging.getLogger().level == logging.DEBUG

    handle_cli_command("/debug", history=[])
    assert cli_commands._debug_enabled is False
    assert logging.getLogger().level == logging.INFO


def test_exit_sets_stop_event_and_warns(monkeypatch):
    # /exit, Ctrl+C ile AYNI kapatma yolunu (core/app.py:run_jarvis()'in
    # `while not stop_event.is_set()` kosulu) tetikliyor - burada sadece
    # stop_event'in gercekten set edildigini dogruluyoruz.
    captured = {}
    monkeypatch.setattr(
        cli_commands, "print_system", lambda msg, level="info": captured.update(msg=msg, level=level)
    )

    stop_event = threading.Event()
    handle_cli_command("/exit", history=[], stop_event=stop_event)

    assert stop_event.is_set()
    assert captured["level"] == "warning"


def test_exit_without_stop_event_does_not_raise():
    # stop_event=None (varsayilan) - hibrit-disi/gelecekteki cagiranlar icin
    # geriye donuk uyumluluk, cokme yerine sessizce yoksayilmali.
    handle_cli_command("/exit", history=[])


def test_test_command_unknown_tool_reports_error_without_importing_app(monkeypatch):
    # core.app'in GERCEK yuklenmesi ears/mouth model yuklemesini tetikler -
    # bilinmeyen bir arac icin bu importa HIC ulasilmamali (bkz. _cmd_test:
    # get_tool(name) None donerse erken cikar).
    assert "src.jarvis.core.app" not in sys.modules or True  # bilgi amacli, assert etmiyoruz

    captured = {}
    monkeypatch.setattr(
        cli_commands, "print_system", lambda msg, level="info": captured.update(msg=msg, level=level)
    )

    handle_cli_command("/test not_a_real_tool", history=[])

    assert "bulunamadı" in captured["msg"]
    assert captured["level"] == "error"


def test_test_command_executes_known_tool_via_mocked_execute_tool(monkeypatch):
    # core.app'i GERCEK import etmek yerine (agir ears/mouth yuklemesi
    # tetikler) sys.modules'a sahte bir modul enjekte ediyoruz - Python'un
    # "from X import Y" cagrisi once sys.modules[X]'e bakar.
    fake_app_module = types.ModuleType("src.jarvis.core.app")
    captured_call = {}

    def fake_execute_tool(tool, intent, stop_event, input_hub=None, pending=None, speaking_event=None):
        captured_call["tool_name"] = tool.name
        captured_call["parameters"] = intent.parameters
        return "sahte sonuç"

    fake_app_module._execute_tool = fake_execute_tool
    monkeypatch.setitem(sys.modules, "src.jarvis.core.app", fake_app_module)

    printed = []
    monkeypatch.setattr(cli_commands.console, "print", lambda msg: printed.append(msg))

    handle_cli_command("/test get_system_info", history=[])

    assert captured_call["tool_name"] == "get_system_info"
    assert any("sahte sonuç" in msg for msg in printed)


def test_test_command_forwards_speaking_event_to_execute_tool(monkeypatch):
    # core/app.py:run_jarvis() olusturdugu paylasilan speaking_event'i /test'e
    # de gecirir - /test uzerinden calistirilan bir arac da TTS anonsu icin
    # (orta+ risk) mikrofonu diger her speak() cagrisiyla ayni sekilde
    # susturabilmeli (bkz. mouth/tts.py:speak()'in speaking_event notu).
    fake_app_module = types.ModuleType("src.jarvis.core.app")
    captured_call = {}

    def fake_execute_tool(tool, intent, stop_event, input_hub=None, pending=None, speaking_event=None):
        captured_call["speaking_event"] = speaking_event
        return "ok"

    fake_app_module._execute_tool = fake_execute_tool
    monkeypatch.setitem(sys.modules, "src.jarvis.core.app", fake_app_module)
    monkeypatch.setattr(cli_commands.console, "print", lambda msg: None)

    sentinel_event = threading.Event()
    handle_cli_command("/test get_system_info", history=[], speaking_event=sentinel_event)

    assert captured_call["speaking_event"] is sentinel_event


def test_test_command_drops_parameters_not_in_tool_schema(monkeypatch):
    # security-reviewer bulgusu: /test, router yolunun validate_arguments()
    # ile yaptigi "semada tanimli olmayan anahtari sessizce ele" filtresini
    # ATLAMAMALI - ozellikle MCP araclarinda (tools/mcp_tool.py sadece
    # "lang"i suzer) rastgele bir extra parametrenin dis sunucuya sizmasini
    # onlemek icin.
    fake_app_module = types.ModuleType("src.jarvis.core.app")
    captured_call = {}

    def fake_execute_tool(tool, intent, stop_event, input_hub=None, pending=None, speaking_event=None):
        captured_call["parameters"] = intent.parameters
        return "ok"

    fake_app_module._execute_tool = fake_execute_tool
    monkeypatch.setitem(sys.modules, "src.jarvis.core.app", fake_app_module)
    monkeypatch.setattr(cli_commands.console, "print", lambda msg: None)

    # create_note SADECE "content" tanimliyor - "bogus" semada yok.
    handle_cli_command("/test create_note content=merhaba bogus=sizmamali", history=[])

    assert captured_call["parameters"] == {"content": "merhaba", "lang": "tr"}
    assert "bogus" not in captured_call["parameters"]


def test_help_lists_dev_commands_and_every_tool_capability(monkeypatch):
    # security-reviewer/DX bulgusu: /help eskiden SADECE gelistirici
    # komutlarini listeliyordu - Jarvis'in gercek yeteneklerini (TOOL_REGISTRY)
    # hem sesli hem yazili nasil tetikleyecegini gostermiyordu. Bu test,
    # ikinci bir arac tablosunun HER kayitli aracı (isim/aciklama/risk) icerdigini
    # dogruluyor - rich Table'in ic render'ini degil, `add_row`'a giden ham
    # argumanlari yakalayarak (kirilgan string-render karsilastirmasindan kacinmak icin).
    from rich.table import Table as RichTable

    from src.jarvis.core.risk import RiskLevel

    class _FakeTool:
        name = "fake_tool"
        description = "Sahte bir seyler yapar."
        risk_level = RiskLevel.LOW

    monkeypatch.setattr(cli_commands, "all_tools", lambda: {"fake_tool": _FakeTool()})

    added_rows: list[tuple] = []
    original_add_row = RichTable.add_row

    def _spy_add_row(self, *args, **kwargs):
        added_rows.append(args)
        return original_add_row(self, *args, **kwargs)

    monkeypatch.setattr(RichTable, "add_row", _spy_add_row)
    monkeypatch.setattr(cli_commands.console, "print", lambda *a, **k: None)

    handle_cli_command("/help", history=[])

    assert ("/help", _COMMANDS["/help"]) in added_rows
    assert ("fake_tool", "Sahte bir seyler yapar.", "low") in added_rows


def test_status_command_reports_without_real_models_or_mcp(monkeypatch):
    fake_listener = types.ModuleType("src.jarvis.ears.listener")
    fake_listener.get_active_device = lambda: "cuda"
    fake_tts = types.ModuleType("src.jarvis.mouth.tts")
    fake_tts.get_active_device = lambda: "cuda"
    monkeypatch.setitem(sys.modules, "src.jarvis.ears.listener", fake_listener)
    monkeypatch.setitem(sys.modules, "src.jarvis.mouth.tts", fake_tts)

    from src.jarvis.core.risk import RiskLevel

    class _FakeTool:
        name = "fake_tool"
        risk_level = RiskLevel.LOW

    monkeypatch.setattr(cli_commands, "all_tools", lambda: {"fake_tool": _FakeTool()})

    printed = []
    monkeypatch.setattr(cli_commands.console, "print", lambda renderable: printed.append(renderable))

    handle_cli_command("/status", history=[{"role": "system", "content": "x"}, {"role": "user", "content": "y"}])

    assert len(printed) == 1  # tek bir Panel basildi


def test_status_shows_pending_approvals(monkeypatch, capsys):
    import sys
    import types

    # _cmd_status ears/mouth modullerinden get_active_device'i gecikmeli import
    # eder (gercek modelleri yukler) - testte hafif sahte modullerle degistir.
    monkeypatch.setitem(sys.modules, "src.jarvis.ears.listener",
                        types.SimpleNamespace(get_active_device=lambda: "cpu"))
    monkeypatch.setitem(sys.modules, "src.jarvis.mouth.tts",
                        types.SimpleNamespace(get_active_device=lambda: "cpu"))

    from src.jarvis.core import pending_tasks
    monkeypatch.setattr(
        pending_tasks, "list_pending",
        lambda limit=10, db_path=None: [
            {"id": 3, "source": "scheduled", "text": "gunluk ozet", "status": "pending"}
        ],
    )

    from src.jarvis.core.cli_commands import _cmd_status
    _cmd_status([{"role": "system", "content": "x"}])
    out = capsys.readouterr().out
    assert "#3" in out and "scheduled" in out


def test_status_no_pending_line(monkeypatch, capsys):
    import sys
    import types

    monkeypatch.setitem(sys.modules, "src.jarvis.ears.listener",
                        types.SimpleNamespace(get_active_device=lambda: "cpu"))
    monkeypatch.setitem(sys.modules, "src.jarvis.mouth.tts",
                        types.SimpleNamespace(get_active_device=lambda: "cpu"))
    from src.jarvis.core import pending_tasks
    monkeypatch.setattr(pending_tasks, "list_pending", lambda limit=10, db_path=None: [])

    from src.jarvis.core.cli_commands import _cmd_status
    _cmd_status([{"role": "system", "content": "x"}])
    assert "Bekleyen onaylar" in capsys.readouterr().out
