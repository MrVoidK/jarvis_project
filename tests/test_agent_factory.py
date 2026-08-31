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
        agent_factory_module._CLIENT,
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
        agent_factory_module._CLIENT,
        "chat",
        lambda model, messages, tools, options=None: {"message": {"tool_calls": [{"unexpected": "shape"}]}},
    )

    response = OllamaAgentAdapter().call_tools("bir şeyler yap", tools=[])

    assert response.tool_calls == []


def test_call_tools_returns_empty_when_arguments_not_a_mapping(monkeypatch):
    """`arguments` bir mapping degilse (orn. bir string) dict(...) ValueError
    firlatir - bu da yakalanip bos listeye dusmeli."""
    monkeypatch.setattr(
        agent_factory_module._CLIENT,
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
        agent_factory_module._CLIENT,
        "chat",
        lambda model, messages, stream=False, tools=None, options=None, keep_alive=None: iter(chunks),
    )

    out = list(OllamaAgentAdapter().respond_stream("selam", context=[]))

    assert out == ["Mer", "haba."]


def test_respond_stream_does_not_swallow_provider_errors(monkeypatch):
    """respond()/call_tools()'un aksine respond_stream saglayici hatasini
    YUTMAMALI - tuketici (brain/llm.py) siniflandiriyor."""

    def _boom(model, messages, stream=False, tools=None, options=None, keep_alive=None):
        raise ConnectionError("ollama down")

    monkeypatch.setattr(agent_factory_module._CLIENT, "chat", _boom)

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


# --- keep_alive mekanizmasi + ClaudeCodeAdapter ---


def test_router_has_bounded_keep_alive_others_default():
    # Router qwen2.5:3b: aktif konusmada sicak kalsin, boste dussun ("30s").
    assert AgentFactory.create("router")._keep_alive == "30s"
    assert AgentFactory.create("orchestrator")._keep_alive is None
    assert AgentFactory.create("tool_agent")._keep_alive is None


def test_call_tools_passes_keep_alive_when_adapter_has_one(monkeypatch):
    captured = {}

    def _fake(model, messages, tools, options=None, keep_alive=None):
        captured["keep_alive"] = keep_alive
        return {"message": {"tool_calls": []}}

    monkeypatch.setattr(agent_factory_module._CLIENT, "chat", _fake)
    # Mekanizma korundu: bir adapter'a keep_alive verilirse call_tools iletir.
    agent_factory_module.OllamaAgentAdapter(keep_alive="2m").call_tools("x", tools=[])
    assert captured["keep_alive"] == "2m"


class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0, timeout=False):
        self._stdout, self._stderr, self.returncode, self._timeout = stdout, stderr, returncode, timeout
        self.pid = 4242

    def communicate(self, timeout=None):
        if self._timeout:
            import subprocess

            raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)
        return self._stdout, self._stderr


def test_claude_code_adapter_respond_runs_cli(monkeypatch):
    from src.jarvis.adapters.agent_factory import ClaudeCodeAdapter
    from src.jarvis.core.paths import PROJECT_ROOT

    seen = {}

    def _fake_popen(args, stdout=None, stderr=None, text=None, cwd=None, env=None):
        seen["args"], seen["cwd"], seen["env"] = args, cwd, env
        return _FakeProc(stdout="analiz metni", returncode=0)

    monkeypatch.setattr(agent_factory_module.subprocess, "Popen", _fake_popen)

    result = ClaudeCodeAdapter().respond("dispatcher.py'yi analiz et")

    assert result == "analiz metni"
    assert seen["args"][:2] == ["claude", "-p"]
    assert seen["cwd"] == PROJECT_ROOT


def test_claude_code_adapter_respond_scrubs_api_key_from_child_env(monkeypatch):
    """Backlog #1: `claude -p` alt sureci ASLA API key kullanmamali (kullanici
    aboneligine dusmeli) - `spawn_detached`'in yaptigi env temizligi burada da
    yapilmali (tutarlilik). `.env`/ortamda bir key olsa bile cocuk env'inde
    olmamali; ilgisiz degiskenler korunmali."""
    from src.jarvis.adapters.agent_factory import ClaudeCodeAdapter

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-scrubbed")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-should-be-scrubbed")
    monkeypatch.setenv("JARVIS_KEEP_THIS", "1")

    seen = {}

    def _fake_popen(args, stdout=None, stderr=None, text=None, cwd=None, env=None):
        seen["env"] = env
        return _FakeProc(stdout="ok", returncode=0)

    monkeypatch.setattr(agent_factory_module.subprocess, "Popen", _fake_popen)

    ClaudeCodeAdapter().respond("bir sey analiz et")

    assert seen["env"] is not None, "env acikca gecilmeli (miras alinmamali)"
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in seen["env"]
    assert seen["env"].get("JARVIS_KEEP_THIS") == "1"


def test_claude_code_adapter_respond_handles_missing_cli(monkeypatch):
    from src.jarvis.adapters.agent_factory import _CLAUDE_CLI_ERROR, ClaudeCodeAdapter

    def _boom(*a, **k):
        raise FileNotFoundError("claude yok")

    monkeypatch.setattr(agent_factory_module.subprocess, "Popen", _boom)
    assert ClaudeCodeAdapter().respond("x") == _CLAUDE_CLI_ERROR


def test_claude_code_adapter_respond_handles_timeout(monkeypatch):
    from src.jarvis.adapters.agent_factory import _CLAUDE_TIMEOUT_MSG, ClaudeCodeAdapter

    monkeypatch.setattr(
        agent_factory_module.subprocess, "Popen", lambda *a, **k: _FakeProc(timeout=True)
    )
    killed = {}
    monkeypatch.setattr(
        agent_factory_module, "_kill_process_tree", lambda proc: killed.setdefault("yes", True)
    )

    assert ClaudeCodeAdapter().respond("x") == _CLAUDE_TIMEOUT_MSG
    assert killed.get("yes")


def test_claude_code_adapter_call_tools_not_implemented():
    from src.jarvis.adapters.agent_factory import ClaudeCodeAdapter

    import pytest

    adapter = ClaudeCodeAdapter()
    assert adapter.supports_tools() is False
    with pytest.raises(NotImplementedError):
        adapter.call_tools("x", tools=[])
