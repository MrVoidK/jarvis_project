"""adapters/agent_factory.py testleri - `ollama.chat` cagrisi monkeypatch'lenir, gercek
Ollama sunucusu gerektirmez.

security-reviewer bulgusu (Faz 3.3): Ollama'nin dondurdugu `tool_calls` beklenmedik
bir bicimde gelirse (KeyError/TypeError'a yol acacak sekilde), bu hicbir yerde
yakalanmadan `run_jarvis()`'e kadar cikip surecin tamamini cokertebilirdi -
`LlamaOrchestratorAdapter.call_tools()` artik bunu yakalayip bos bir
`AgentToolResponse` donduruyor (fail-safe, crash degil).
"""

from src.jarvis.adapters import agent_factory as agent_factory_module
from src.jarvis.adapters.agent_factory import LlamaOrchestratorAdapter


def test_call_tools_parses_well_formed_tool_call(monkeypatch):
    monkeypatch.setattr(
        agent_factory_module.ollama,
        "chat",
        lambda model, messages, tools: {
            "message": {"tool_calls": [{"function": {"name": "get_time", "arguments": {}}}]}
        },
    )

    response = LlamaOrchestratorAdapter().call_tools("saat kaç?", tools=[])

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "get_time"


def test_call_tools_returns_empty_when_tool_calls_missing_function_key(monkeypatch):
    """Beklenmeyen bicim (orn. 'function' anahtari yok) - KeyError yakalanip
    bos bir liste donmeli, exception yukari cikmamali."""
    monkeypatch.setattr(
        agent_factory_module.ollama,
        "chat",
        lambda model, messages, tools: {"message": {"tool_calls": [{"unexpected": "shape"}]}},
    )

    response = LlamaOrchestratorAdapter().call_tools("bir şeyler yap", tools=[])

    assert response.tool_calls == []


def test_call_tools_returns_empty_when_arguments_not_a_mapping(monkeypatch):
    """`arguments` bir mapping degilse (orn. bir string) dict(...) ValueError
    firlatir - bu da yakalanip bos listeye dusmeli."""
    monkeypatch.setattr(
        agent_factory_module.ollama,
        "chat",
        lambda model, messages, tools: {
            "message": {"tool_calls": [{"function": {"name": "get_time", "arguments": "not-a-dict"}}]}
        },
    )

    response = LlamaOrchestratorAdapter().call_tools("saat kaç?", tools=[])

    assert response.tool_calls == []
