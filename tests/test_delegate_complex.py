"""_run_delegate_complex (çok adımlı delegasyon döngüsü) testleri - gerçek
Ollama/onay OLMADAN.

2026-08-29 Cluster F-hafif: `_MAX_DELEGATE_STEPS 3→5`, `_TOOL_AGENT_SYSTEM_PROMPT`
netleştirildi. Router few-shot artışı Cluster C ile birlikte (dispatcher.py).
"""

import src.jarvis.core.app as app
from src.jarvis.agents.base import AgentToolResponse, ToolCall
from src.jarvis.core.dispatcher import DELEGATE_COMPLEX_INTENT_NAME, Intent


def _intent(task: str) -> Intent:
    return Intent(
        name=DELEGATE_COMPLEX_INTENT_NAME,
        confidence=0.7,
        source="llm",
        parameters={"task": task, "lang": "tr"},
    )


def test_max_delegate_steps_is_five():
    assert app._MAX_DELEGATE_STEPS == 5


def test_delegate_complex_completes_when_agent_finishes(monkeypatch):
    calls = {"n": 0}

    class _FakeAgent:
        def call_tools(self, prompt, tools, context=None):
            calls["n"] += 1
            if calls["n"] < 4:  # 3 araç adımı
                return AgentToolResponse(
                    tool_calls=[ToolCall(name="get_system_info", arguments={})]
                )
            return AgentToolResponse(
                tool_calls=[ToolCall(name=app._NO_TOOL_FUNCTION_NAME, arguments={})],
                content="Sistem durumuna baktım ve özetledim.",
            )

    monkeypatch.setattr(app.AgentFactory, "create", staticmethod(lambda role: _FakeAgent()))
    monkeypatch.setattr(app, "_execute_tool", lambda *a, **k: "araç sonucu")

    out = list(app._run_delegate_complex(_intent("sistem durumuna bak ve özetle"), None, None, None, None, None))

    assert calls["n"] == 4  # 3 araç + 1 no_tool_needed
    assert out[-1][0] == "Sistem durumuna baktım ve özetledim."


def test_delegate_complex_stops_at_step_cap(monkeypatch):
    class _NeverFinishes:
        def call_tools(self, prompt, tools, context=None):
            return AgentToolResponse(tool_calls=[ToolCall(name="get_system_info", arguments={})])

    monkeypatch.setattr(app.AgentFactory, "create", staticmethod(lambda role: _NeverFinishes()))
    seen = {"n": 0}

    def _fake_exec(*a, **k):
        seen["n"] += 1
        return f"sonuç {seen['n']}"

    monkeypatch.setattr(app, "_execute_tool", _fake_exec)

    out = list(app._run_delegate_complex(_intent("durmadan araç çağır"), None, None, None, None, None))

    assert seen["n"] == app._MAX_DELEGATE_STEPS  # tam 5 adımda durur
    assert out[-1][0] == "sonuç 5"
