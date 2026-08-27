"""Faz 3 arac katmani testleri - tool'lar, risk puanlama ve dispatcher kalip eslesmesi.

Dosya yazan araclar (notes, files) `monkeypatch` ile gecici bir dizine yonlendiriliyor -
testler gercek Obsidian vault'una veya jarvis_workspace/ dizinine dokunmaz.

Calistirma: `python -m pytest tests/ -v` (repo kokunden, bkz. CLAUDE.md Komutlar).
"""

import time
from types import SimpleNamespace

from src.jarvis.core import app as app_module
from src.jarvis.core.dispatcher import SHUTDOWN_INTENT_NAME, Dispatcher
from src.jarvis.core.risk import RiskLevel, evaluate_approval_answer, requires_approval
from src.jarvis.tools import files as files_module
from src.jarvis.tools import notes_tool as notes_module
from src.jarvis.tools.registry import TOOL_REGISTRY
from src.jarvis.tools.terminal_tool import LaunchAppTool, RunCommandTool

# --- risk puanlama ---


def test_only_low_risk_skips_approval():
    assert not requires_approval(RiskLevel.LOW)
    for level in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL):
        assert requires_approval(level), f"{level} onaysiz gecmemeli"


def test_shell_tool_is_always_high_risk():
    # Terminal komutu, icerigi ne olursa olsun ISTISNASIZ onay istemeli
    # (bkz. tools/terminal_tool.py modul docstring'i, 1. katman).
    assert RunCommandTool.risk_level is RiskLevel.HIGH


def test_registry_keys_match_tool_names():
    for name, tool in TOOL_REGISTRY.items():
        assert name == tool.name


def test_evaluate_approval_answer_accepts_known_affirmatives():
    # core/input_hub.py'nin hibrit onay yolunun kullandigi fonksiyon -
    # request_approval()'in stdin okumadan, disaridan hazir bir cevap
    # metnini yorumlayan karsiligi.
    for answer in ("y", "Y", "yes", "YES", "e", "evet", " Evet "):
        assert evaluate_approval_answer(answer), f"{answer!r} onay sayilmali"


def test_evaluate_approval_answer_defaults_to_reject():
    for answer in ("n", "no", "hayir", "", "  ", "maybe"):
        assert not evaluate_approval_answer(answer), f"{answer!r} red sayilmali"


# --- notes ---


def _patch_vault(monkeypatch, tmp_path):
    """notes_tool'u sahte bir vault'a yonlendirir - gercek Obsidian vault'una dokunulmaz."""
    vault = tmp_path / "vault"
    monkeypatch.setattr(notes_module, "get_obsidian_vault", lambda: vault)
    monkeypatch.setattr(notes_module, "is_path_safe", lambda path: True)
    return vault


def test_create_and_read_note(monkeypatch, tmp_path):
    _patch_vault(monkeypatch, tmp_path)

    create = notes_module.CreateNoteTool()
    read = notes_module.ReadNotesTool()

    assert "yok" in read.execute({"lang": "tr"})  # henuz not yok

    create.execute({"lang": "tr", "content": "sut al"})
    create.execute({"lang": "tr", "content": "faturayi ode"})

    result = read.execute({"lang": "tr"})
    assert "sut al" in result
    assert "faturayi ode" in result


def test_create_note_rejects_empty_content(monkeypatch, tmp_path):
    vault = _patch_vault(monkeypatch, tmp_path)

    result = notes_module.CreateNoteTool().execute({"lang": "en", "content": "   "})
    assert "empty" in result.lower()
    assert not (vault / notes_module.NOTES_SUBDIR).exists()  # dosya hic olusturulmamali


def test_create_note_blocked_by_unsafe_path(monkeypatch, tmp_path):
    """is_path_safe() False donerse not YAZILMAMALI - security.yaml yanlis
    yapilandirilsa bile vault disina yazma engellenir (bkz. modul docstring'i)."""
    vault = tmp_path / "vault"
    monkeypatch.setattr(notes_module, "get_obsidian_vault", lambda: vault)
    monkeypatch.setattr(notes_module, "is_path_safe", lambda path: False)

    result = notes_module.CreateNoteTool().execute({"lang": "en", "content": "test"})
    assert "security" in result.lower() or "güvenlik" in result.lower()
    assert not vault.exists()


# --- files ---


def test_list_files_reports_empty_and_populated(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(files_module, "WORKSPACE_DIR", str(workspace))
    tool = files_module.ListFilesTool()

    assert "empty" in tool.execute({"lang": "en"}).lower()

    (workspace / "rapor.txt").write_text("x", encoding="utf-8")
    assert "rapor.txt" in tool.execute({"lang": "en"})


# --- terminal (run_command) ---


def test_run_command_executes_and_returns_output():
    result = RunCommandTool().execute({"lang": "en", "command": "echo jarvis_test_ok"})
    assert "jarvis_test_ok" in result


def test_run_command_rejects_empty_command():
    result = RunCommandTool().execute({"lang": "en", "command": ""})
    assert "didn't get a command" in result.lower()


def test_run_command_strips_trailing_punctuation():
    """Regresyon: docs/TODO.md madde 1 - "Run command ls." icin STT'nin ekledigi
    sondaki nokta command'a karisip Windows'ta 'ls.' calistirilmaya calisiliyordu."""
    result = RunCommandTool().execute({"lang": "en", "command": "echo jarvis_test_ok."})
    assert "jarvis_test_ok" in result
    assert "not recognized" not in result.lower()


# --- terminal (launch_app) ---


def test_launch_app_starts_known_application(monkeypatch):
    from src.jarvis.tools import terminal_tool as terminal_module

    monkeypatch.setattr(terminal_module, "resolve_app_command", lambda name: "code")
    calls: list[tuple] = []
    monkeypatch.setattr(
        terminal_module.subprocess, "Popen", lambda command, shell=False: calls.append((command, shell))
    )

    result = LaunchAppTool().execute({"lang": "en", "app_name": "vs code"})

    assert calls == [("code", True)]
    assert "vs code" in result.lower()
    assert "code" not in result.lower().replace("vs code", "")  # cozulmus binary ismi konusmaya SIZMAMALI


def test_launch_app_rejects_unknown_application(monkeypatch):
    from src.jarvis.tools import terminal_tool as terminal_module

    monkeypatch.setattr(terminal_module, "resolve_app_command", lambda name: None)
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("bilinmeyen uygulamada Popen cagrilmamali")),
    )

    result = LaunchAppTool().execute({"lang": "tr", "app_name": "discord"})
    assert "bilmiyorum" in result.lower() or "bulamadım" in result.lower()


def test_launch_app_is_medium_risk():
    assert LaunchAppTool.risk_level is RiskLevel.MEDIUM


# --- dispatcher kalip eslesmesi ---


def test_dispatcher_match_rule_extracts_get_time_language():
    dispatcher = Dispatcher()

    time_tr = dispatcher.match_rule("saat kaç?")
    assert time_tr.name == "get_time"
    assert time_tr.parameters["lang"] == "tr"

    time_en = dispatcher.match_rule("what time is it?")
    assert time_en.name == "get_time"
    assert time_en.parameters["lang"] == "en"


def test_dispatcher_returns_none_for_plain_chat():
    assert Dispatcher().match_rule("bugün hava nasıl?") is None


def test_dispatcher_match_rule_extracts_shutdown_language():
    """Sesli/yazili "sistemi kapat" - core/app.py'nin `while not stop_event.
    is_set()` kapatma yolunu tetikleyen fast-path kural (bkz. SHUTDOWN_INTENT_NAME);
    LLM'e gitmiyor, get_time ile ayni gerekce (belirsizlik tasimamali)."""
    dispatcher = Dispatcher()

    shutdown_tr = dispatcher.match_rule("sistemi kapat")
    assert shutdown_tr.name == SHUTDOWN_INTENT_NAME
    assert shutdown_tr.parameters["lang"] == "tr"

    shutdown_en = dispatcher.match_rule("please shut down")
    assert shutdown_en.name == SHUTDOWN_INTENT_NAME
    assert shutdown_en.parameters["lang"] == "en"

    shutdown_en_alt = dispatcher.match_rule("turn yourself off")
    assert shutdown_en_alt.name == SHUTDOWN_INTENT_NAME
    assert shutdown_en_alt.parameters["lang"] == "en"


def test_dispatcher_match_rule_no_longer_handles_former_regex_intents():
    """Regresyon: Faz 3.3 semantic router gecisiyle _RULES sadece get_time'a
    indirildi (bkz. core/dispatcher.py). create_note/run_command/read_notes/
    get_system_info/play_music/pause_music/skip_track artik match_rule() ile
    DEGIL, Dispatcher.classify()'in semantic router yolu (bkz.
    tests/test_dispatcher_router.py) ile eslesiyor - match_rule bunlar icin
    artik None donmeli (Brain'e degil, router'a devredildi)."""
    dispatcher = Dispatcher()

    for text in [
        "not tut: yarın toplantı var",
        "take a note: buy milk",
        "run command: dir",
        "notlarımı oku",
        "read my notes",
        "sistem durumu nedir",
        "system status",
        "şarkı çal: Bohemian Rhapsody",
        "pause music",
        "şarkıyı geç",
    ]:
        assert dispatcher.match_rule(text) is None, f"artik match_rule ile eslesmemeli: {text!r}"


# Not: Spotify entegrasyonu (tools/spotify.py) kaldirildi - muzik kontrolu artik
# tools/media_tool.py (yerel Windows medya tuslari) uzerinden. Dispatcher'in
# play_music/pause_music/skip_track regex testleri, _RULES semantic router'a
# devredilirken (bkz. sonraki commit) buradan kaldirilacak.


# --- tool zaman asimi sarmalayicisi (Faz 6.1) ---


class _FakeTool:
    """_run_tool_pipeline sadece .name/.risk_level/.execute'e bakiyor - gercek
    bir Tool alt sinifi sart degil. LOW risk -> onay yolu atlanir."""

    risk_level = RiskLevel.LOW

    def __init__(self, name, execute_fn):
        self.name = name
        self._execute_fn = execute_fn

    def execute(self, params, stop_event=None):
        return self._execute_fn(params)


def _fake_intent():
    return SimpleNamespace(name="fake", parameters={"lang": "en"})


def test_tool_execution_times_out(monkeypatch):
    """Donmus bir tool, _TOOL_EXEC_TIMEOUT_SECONDS'ten fazla ana dongu bloklamaz;
    kullaniciya lokalize zaman-asimi mesaji donulur."""
    monkeypatch.setattr(app_module, "_TOOL_EXEC_TIMEOUT_SECONDS", 0.5)
    tool = _FakeTool("slow_tool", lambda params: time.sleep(5) or "gec kalan sonuc")

    started = time.monotonic()
    result = app_module._run_tool_pipeline(tool, _fake_intent(), stop_event=None)
    elapsed = time.monotonic() - started

    assert result == app_module._TOOL_TIMEOUT_MESSAGES["en"]
    assert elapsed < 3.0, f"timeout sarmalayicisi beklemeyi kesmedi ({elapsed:.1f}s)"


def test_tool_execution_normal_path_unaffected(monkeypatch):
    """Hizli bir tool'un sonucu, sarmalayici eklendikten sonra da aynen doner."""
    monkeypatch.setattr(app_module, "_TOOL_EXEC_TIMEOUT_SECONDS", 5.0)
    tool = _FakeTool("fast_tool", lambda params: "hepsi yolunda")

    result = app_module._run_tool_pipeline(tool, _fake_intent(), stop_event=None)

    assert result == "hepsi yolunda"


# --- Faz 6.3: delegate dallari ---


class _ScriptedAgent:
    """call_tools() onceden yazilmis AgentToolResponse'lari sirayla dondurur."""

    def __init__(self, responses):
        self._responses = list(responses)

    def call_tools(self, prompt, tools, context=None):
        return self._responses.pop(0)


def _delegate_intent(name, task):
    return SimpleNamespace(name=name, parameters={"task": task, "lang": "en"})


def test_delegate_complex_runs_bounded_loop(monkeypatch):
    from src.jarvis.agents.base import AgentToolResponse, ToolCall

    fake_tool = SimpleNamespace(name="get_system_info", parameters_schema={}, risk_level=RiskLevel.LOW)
    monkeypatch.setattr(app_module, "all_tools", lambda: {})
    monkeypatch.setattr(app_module, "get_tool", lambda n: fake_tool if n == "get_system_info" else None)
    executed = []
    monkeypatch.setattr(
        app_module, "_execute_tool",
        lambda tool, intent, *a, **k: executed.append(intent.name) or "adım tamam",
    )
    agent = _ScriptedAgent([
        AgentToolResponse(tool_calls=[ToolCall(name="get_system_info", arguments={})]),
        AgentToolResponse(
            tool_calls=[ToolCall(name=app_module._NO_TOOL_FUNCTION_NAME, arguments={})],
            content="durum iyi, not aldım",
        ),
    ])
    monkeypatch.setattr(app_module.AgentFactory, "create", staticmethod(lambda role: agent))

    out = list(app_module._run_delegate_complex(
        _delegate_intent("delegate_complex", "durumu kontrol et ve not al"),
        None, None, None, None, None,
    ))

    assert out == [("durum iyi, not aldım", "en")]
    assert executed == ["get_system_info"]


def test_delegate_complex_stops_at_max_steps(monkeypatch):
    from src.jarvis.agents.base import AgentToolResponse, ToolCall

    fake_tool = SimpleNamespace(name="get_system_info", parameters_schema={}, risk_level=RiskLevel.LOW)
    monkeypatch.setattr(app_module, "all_tools", lambda: {})
    monkeypatch.setattr(app_module, "get_tool", lambda n: fake_tool)
    executed = []
    monkeypatch.setattr(
        app_module, "_execute_tool",
        lambda tool, intent, *a, **k: executed.append(1) or "adım sonucu",
    )
    agent = _ScriptedAgent(
        [AgentToolResponse(tool_calls=[ToolCall(name="get_system_info", arguments={})])] * 5
    )
    monkeypatch.setattr(app_module.AgentFactory, "create", staticmethod(lambda role: agent))

    out = list(app_module._run_delegate_complex(
        _delegate_intent("delegate_complex", "durmadan çalış"), None, None, None, None, None,
    ))

    assert len(executed) == app_module._MAX_DELEGATE_STEPS
    assert out == [("adım sonucu", "en")]


def test_delegate_complex_guardrail_blocks_task(monkeypatch):
    monkeypatch.setattr(
        app_module._INPUT_GUARDRAIL, "run",
        lambda t: SimpleNamespace(allowed=False, reason="blocked"),
    )
    executed = []
    monkeypatch.setattr(app_module, "_execute_tool", lambda *a, **k: executed.append(1))

    out = list(app_module._run_delegate_complex(
        _delegate_intent("delegate_complex", "kötü şey"), None, None, None, None, None,
    ))

    assert out == [(app_module._DELEGATE_FAILED_MESSAGES["en"], "en")]
    assert executed == []


def test_delegate_code_approved_runs_and_yields(monkeypatch):
    monkeypatch.setattr(app_module, "_prompt_for_approval", lambda *a, **k: True)
    monkeypatch.setattr(app_module, "speak", lambda *a, **k: None)
    stub = SimpleNamespace(respond=lambda task: "hata dispatcher tarafinda")
    monkeypatch.setattr(app_module.AgentFactory, "create", staticmethod(lambda role: stub))

    out = list(app_module._run_delegate_code(
        _delegate_intent("delegate_code", "dispatcher.py'yi analiz et"), None, None, None, None,
    ))

    assert out == [("hata dispatcher tarafinda", "en")]


def test_delegate_code_denied_does_not_run(monkeypatch):
    monkeypatch.setattr(app_module, "_prompt_for_approval", lambda *a, **k: False)
    monkeypatch.setattr(app_module, "speak", lambda *a, **k: None)
    called = []
    stub = SimpleNamespace(respond=lambda task: called.append(1) or "x")
    monkeypatch.setattr(app_module.AgentFactory, "create", staticmethod(lambda role: stub))

    out = list(app_module._run_delegate_code(
        _delegate_intent("delegate_code", "analiz et"), None, None, None, None,
    ))

    assert out == [(app_module._APPROVAL_DENIED_MESSAGES["en"], "en")]
    assert called == []
