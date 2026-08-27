"""adapters/agent_factory.py testleri - `ollama.chat` cagrisi monkeypatch'lenir, gercek
Ollama sunucusu gerektirmez.

security-reviewer bulgusu (Faz 3.3): Ollama'nin dondurdugu `tool_calls` beklenmedik
bir bicimde gelirse (KeyError/TypeError'a yol acacak sekilde), bu hicbir yerde
yakalanmadan `run_jarvis()`'e kadar cikip surecin tamamini cokertebilirdi -
`OllamaAgentAdapter.call_tools()` artik bunu yakalayip bos bir
`AgentToolResponse` donduruyor (fail-safe, crash degil).
"""

from src.jarvis.adapters import agent_factory as agent_factory_module
from src.jarvis.adapters.agent_factory import (
    AgentFactory,
    ClaudeCodeAdapter,
    OllamaAgentAdapter,
)


def test_call_tools_parses_well_formed_tool_call(monkeypatch):
    monkeypatch.setattr(
        agent_factory_module.ollama,
        "chat",
        lambda model, messages, tools, options=None: {
            "message": {"tool_calls": [{"function": {"name": "get_time", "arguments": {}}}]}
        },
    )

    response = OllamaAgentAdapter().call_tools("saat kaç?", tools=[])

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "get_time"


def test_call_tools_returns_empty_when_tool_calls_missing_function_key(monkeypatch):
    """Beklenmeyen bicim (orn. 'function' anahtari yok) - KeyError yakalanip
    bos bir liste donmeli, exception yukari cikmamali."""
    monkeypatch.setattr(
        agent_factory_module.ollama,
        "chat",
        lambda model, messages, tools, options=None: {"message": {"tool_calls": [{"unexpected": "shape"}]}},
    )

    response = OllamaAgentAdapter().call_tools("bir şeyler yap", tools=[])

    assert response.tool_calls == []


def test_call_tools_returns_empty_when_arguments_not_a_mapping(monkeypatch):
    """`arguments` bir mapping degilse (orn. bir string) dict(...) ValueError
    firlatir - bu da yakalanip bos listeye dusmeli."""
    monkeypatch.setattr(
        agent_factory_module.ollama,
        "chat",
        lambda model, messages, tools, options=None: {
            "message": {"tool_calls": [{"function": {"name": "get_time", "arguments": "not-a-dict"}}]}
        },
    )

    response = OllamaAgentAdapter().call_tools("saat kaç?", tools=[])

    assert response.tool_calls == []


def test_respond_stream_yields_raw_chunks(monkeypatch):
    """respond_stream(), ollama.chat(stream=True)'in her chunk'inin ham icerigini
    (cumle bolmeden) yield etmeli - cumle bolme brain/llm.py'nin isi."""
    chunks = [{"message": {"content": "Mer"}}, {"message": {"content": "haba."}}]
    monkeypatch.setattr(
        agent_factory_module.ollama,
        "chat",
        lambda model, messages, stream=False, tools=None, options=None: iter(chunks),
    )

    out = list(OllamaAgentAdapter().respond_stream("selam", context=[]))

    assert out == ["Mer", "haba."]


def test_respond_stream_does_not_swallow_provider_errors(monkeypatch):
    """respond()/call_tools()'un aksine respond_stream saglayici hatasini
    YUTMAMALI - tuketici (brain/llm.py) siniflandiriyor."""

    def _boom(model, messages, stream=False, tools=None, options=None):
        raise ConnectionError("ollama down")

    monkeypatch.setattr(agent_factory_module.ollama, "chat", _boom)

    import pytest

    with pytest.raises(ConnectionError):
        list(OllamaAgentAdapter().respond_stream("selam", context=[]))


def test_factory_maps_roles_to_models():
    assert AgentFactory.create("orchestrator")._model_name == "hermes3:8b"
    assert AgentFactory.create("tool_agent")._model_name == "hermes3:8b"
    assert AgentFactory.create("router")._model_name == "qwen2.5:3b"
    assert isinstance(AgentFactory.create("deep_reasoning"), ClaudeCodeAdapter)


def test_factory_rejects_unknown_role():
    import pytest

    with pytest.raises(ValueError):
        AgentFactory.create("bogus")
