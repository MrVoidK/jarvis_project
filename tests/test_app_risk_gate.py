"""Faz 6.6 runtime risk kapisi - scheduled/continuous + MEDIUM+ -> pending kayit,
arac calismaz. voice/text davranisi degismez."""

import pytest

import src.jarvis.core.app as app
from src.jarvis.core.dispatcher import (
    DELEGATE_CODE_INTENT_NAME,
    DELEGATE_COMPLEX_INTENT_NAME,
    Intent,
)
from src.jarvis.core.risk import RiskLevel
from src.jarvis.tools.base import Tool

_HISTORY = [{"role": "system", "content": "x"}]


class _FakeTool(Tool):
    def __init__(self, name: str, risk: RiskLevel):
        self.name = name
        self.description = "fake"
        self.risk_level = risk
        self.executed = False

    def execute(self, params: dict, stop_event=None) -> str:
        self.executed = True
        return "calisti"


def _stub_dispatch(monkeypatch, tool: _FakeTool):
    monkeypatch.setattr(app, "get_tool", lambda name: tool if name == tool.name else None)
    monkeypatch.setattr(
        app._DISPATCHER,
        "classify",
        lambda text: Intent(name=tool.name, confidence=0.9, source="rule", parameters={"lang": "tr"}),
    )


def test_scheduled_medium_tool_records_pending_and_skips_execution(monkeypatch):
    tool = _FakeTool("danger", RiskLevel.MEDIUM)
    _stub_dispatch(monkeypatch, tool)
    recorded = []
    monkeypatch.setattr(
        app, "record_pending",
        lambda source, text, detail: recorded.append((source, text, detail)) or 7,
    )

    out = list(app._handle_turn("zamanlanmis komut", _HISTORY, source="scheduled"))

    assert tool.executed is False
    assert recorded == [("scheduled", "zamanlanmis komut",
                         {"tool": "danger", "risk": "medium", "params": {}})]
    assert out and "onay" in out[0][0].lower()


def test_scheduled_low_tool_runs(monkeypatch):
    tool = _FakeTool("safe", RiskLevel.LOW)
    _stub_dispatch(monkeypatch, tool)
    monkeypatch.setattr(app, "record_pending", lambda *a, **k: 1)

    list(app._handle_turn("zamanlanmis komut", _HISTORY, source="scheduled"))

    assert tool.executed is True


def test_voice_medium_tool_still_uses_approval_path(monkeypatch):
    tool = _FakeTool("danger", RiskLevel.MEDIUM)
    _stub_dispatch(monkeypatch, tool)
    called = {}
    monkeypatch.setattr(app, "_prompt_for_approval",
                        lambda *a, **k: called.update({"asked": True}) or False)
    monkeypatch.setattr(app, "record_pending",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("voice yolu record_pending cagirmamali")))

    list(app._handle_turn("normal komut", _HISTORY))  # source varsayilani "voice"

    assert called.get("asked") is True
    assert tool.executed is False  # _prompt_for_approval False dondu (onay reddedildi)


# --- Faz 6.6 final review (CRITICAL #1): scheduled/continuous + delege intent ---


def _stub_delegate_dispatch(monkeypatch, intent_name: str):
    """Dispatcher'i verilen delege intent'ini secmis gibi sabitler + interaktif
    onay yolunu (cagirilirsa test patlasin) devre disi birakir."""
    monkeypatch.setattr(
        app._DISPATCHER,
        "classify",
        lambda text: Intent(
            name=intent_name, confidence=0.7, source="rule",
            parameters={"task": "cok adimli bir sey yap", "lang": "tr"},
        ),
    )
    monkeypatch.setattr(
        app, "_prompt_for_approval",
        lambda *a, **k: pytest.fail("scheduled delege guard'i _prompt_for_approval cagirmamali"),
    )


def test_scheduled_delegate_complex_records_pending_not_prompt(monkeypatch):
    _stub_delegate_dispatch(monkeypatch, DELEGATE_COMPLEX_INTENT_NAME)
    recorded = []
    monkeypatch.setattr(
        app, "record_pending",
        lambda source, text, detail: recorded.append((source, text, detail)) or 11,
    )

    out = list(app._handle_turn("zamanlanmis delege", _HISTORY, source="scheduled"))

    assert len(recorded) == 1
    assert recorded[0][0] == "scheduled"
    assert recorded[0][2]["tool"] == DELEGATE_COMPLEX_INTENT_NAME
    assert out and "onay" in out[0][0].lower()


def test_scheduled_delegate_code_records_pending_not_prompt(monkeypatch):
    _stub_delegate_dispatch(monkeypatch, DELEGATE_CODE_INTENT_NAME)
    recorded = []
    monkeypatch.setattr(
        app, "record_pending",
        lambda source, text, detail: recorded.append((source, text, detail)) or 12,
    )

    out = list(app._handle_turn("zamanlanmis kod gorevi", _HISTORY, source="continuous"))

    assert len(recorded) == 1
    assert recorded[0][0] == "continuous"
    assert recorded[0][2]["tool"] == DELEGATE_CODE_INTENT_NAME
    assert out and "onay" in out[0][0].lower()


def test_voice_delegate_complex_still_runs(monkeypatch):
    _stub_delegate_dispatch(monkeypatch, DELEGATE_COMPLEX_INTENT_NAME)
    monkeypatch.setattr(
        app, "record_pending",
        lambda *a, **k: pytest.fail("voice delege yolu record_pending cagirmamali"),
    )
    marker = {}

    def _fake_delegate(*a, **k):
        marker["reached"] = True
        yield "delege calisti", "tr"

    monkeypatch.setattr(app, "_run_delegate_complex", _fake_delegate)

    out = list(app._handle_turn("normal delege", _HISTORY))  # source varsayilani "voice"

    assert marker.get("reached") is True
    assert out == [("delege calisti", "tr")]


def test_scheduled_pending_gate_emits_no_hud_tool_event(monkeypatch):
    tool = _FakeTool("danger", RiskLevel.MEDIUM)
    _stub_dispatch(monkeypatch, tool)
    monkeypatch.setattr(app, "record_pending", lambda *a, **k: 7)
    hud_calls = []
    monkeypatch.setattr(app.hud_bus, "publish_tool", lambda *a, **k: hud_calls.append((a, k)))

    list(app._handle_turn("zamanlanmis komut", _HISTORY, source="scheduled"))

    assert tool.executed is False
    assert not any(a and a[0] == "start" for a, _ in hud_calls)
