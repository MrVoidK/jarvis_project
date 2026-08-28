"""Faz 6.6 runtime risk kapisi - scheduled/continuous + MEDIUM+ -> pending kayit,
arac calismaz. voice/text davranisi degismez."""

import src.jarvis.core.app as app
from src.jarvis.core.dispatcher import Intent
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
