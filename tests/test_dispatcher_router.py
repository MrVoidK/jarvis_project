"""Semantic router (Dispatcher.classify) testleri - gercek Ollama cagrisi YAPILMAZ.

AgentFactory.create("orchestrator") monkeypatch'lenip sahte bir Agent (call_tools()
onceden belirlenmis bir AgentToolResponse donduren) ile degistiriliyor - testler
Ollama'nin calisiyor olmasina bagimli degil.

Calistirma: `python -m pytest tests/ -v` (repo kokunden, bkz. CLAUDE.md Komutlar).
"""

from src.jarvis.agents.base import AgentToolResponse, ToolCall
from src.jarvis.core import dispatcher as dispatcher_module
from src.jarvis.core.dispatcher import DEFAULT_INTENT_NAME, Dispatcher


class _StubOrchestrator:
    def __init__(self, response: AgentToolResponse):
        self._response = response

    def call_tools(self, prompt, tools, context=None):
        return self._response


def _patch_orchestrator(monkeypatch, response: AgentToolResponse):
    monkeypatch.setattr(
        dispatcher_module.AgentFactory, "create", staticmethod(lambda role: _StubOrchestrator(response))
    )


def test_classify_fast_path_never_calls_agent_factory(monkeypatch):
    # get_time _RULES'ta oldugu icin AgentFactory.create HIC cagrilmamali.
    monkeypatch.setattr(
        dispatcher_module.AgentFactory,
        "create",
        staticmethod(lambda role: (_ for _ in ()).throw(AssertionError("cagrilmamali"))),
    )

    intent = Dispatcher().classify("saat kaç?")
    assert intent.name == "get_time"
    assert intent.source == "rule"


def test_classify_returns_known_tool_selected_by_router(monkeypatch):
    _patch_orchestrator(
        monkeypatch,
        AgentToolResponse(tool_calls=[ToolCall(name="media_next_track", arguments={})]),
    )

    intent = Dispatcher().classify("bir sonraki şarkıya geç")

    assert intent.name == "media_next_track"
    assert intent.source == "llm"
    assert intent.confidence > 0.5
    assert "lang" in intent.parameters


def test_classify_passes_through_tool_arguments(monkeypatch):
    _patch_orchestrator(
        monkeypatch,
        AgentToolResponse(tool_calls=[ToolCall(name="create_note", arguments={"content": "sut al"})]),
    )

    intent = Dispatcher().classify("bir not al: süt al")

    assert intent.name == "create_note"
    assert intent.parameters["content"] == "sut al"


def test_classify_falls_back_to_chat_when_no_tool_selected(monkeypatch):
    _patch_orchestrator(monkeypatch, AgentToolResponse(tool_calls=[]))

    intent = Dispatcher().classify("bugün hava nasıl?")

    assert intent.name == DEFAULT_INTENT_NAME
    assert intent.source == "llm"


def test_classify_falls_back_to_chat_when_router_hallucinates_unknown_tool(monkeypatch):
    _patch_orchestrator(
        monkeypatch,
        AgentToolResponse(tool_calls=[ToolCall(name="not_a_real_tool", arguments={})]),
    )

    intent = Dispatcher().classify("bir şeyler yap")

    assert intent.name == DEFAULT_INTENT_NAME
    assert intent.source == "llm"


def test_classify_falls_back_to_chat_when_router_produces_wrong_argument_type(monkeypatch):
    """security-reviewer bulgusu (Faz 3.3): kucuk yerel modeller JSON-Schema'ya
    her zaman uymayabilir - "content" bir string yerine bir liste gelirse
    (fail-closed validate_arguments) tum cagri reddedilmeli, dogrulanmamis
    deger asla Intent.parameters'a ulasmamali."""
    _patch_orchestrator(
        monkeypatch,
        AgentToolResponse(
            tool_calls=[ToolCall(name="create_note", arguments={"content": ["rm", "-rf", "/"]})]
        ),
    )

    intent = Dispatcher().classify("bir not al")

    assert intent.name == DEFAULT_INTENT_NAME
    assert intent.source == "llm"
