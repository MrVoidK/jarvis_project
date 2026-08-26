"""MCP entegrasyonu testleri - mcp_tool.py, mcp_config.py, _wrap_mcp_tool().

Gercek bir MCP sunucusuna/npx alt surecine HIC baglanilmiyor - `MCPTool`
constructor injection ile senkron bir sahte `call_fn` alir (bkz.
tools/mcp_tool.py modul docstring'i), `_wrap_mcp_tool()` de dogrudan bir
`mcp.types.Tool` nesnesiyle (IO'suz, saf esleme) test edilir.

Calistirma: `python -m pytest tests/ -v` (repo kokunden, bkz. CLAUDE.md Komutlar).
"""

import pytest
from mcp.types import Tool as MCPToolSchema

from src.jarvis.adapters.mcp_client_adapter import _wrap_mcp_tool
from src.jarvis.core.mcp_config import MCPServerConfig, load_mcp_servers_config
from src.jarvis.core.risk import RiskLevel
from src.jarvis.tools.mcp_tool import MCPTool, build_mcp_tool_name

# --- build_mcp_tool_name ---


def test_build_mcp_tool_name_prefixes_and_sanitizes():
    assert build_mcp_tool_name("filesystem", "read_text_file") == "mcp_filesystem_read_text_file"
    # Boslukta/ozel karakterde patlamamali - onay panelinde/loglarda gorunecek
    # isim her zaman alfanumerik+altcizgi kalmali.
    assert build_mcp_tool_name("My Server!", "some tool") == "mcp_my_server__some_tool"


# --- MCPTool.execute() ---


def test_mcp_tool_execute_strips_lang_before_forwarding():
    captured = {}

    def fake_call(arguments: dict) -> str:
        captured.update(arguments)
        return "ok"

    tool = MCPTool(
        name="mcp_filesystem_read_text_file",
        description="test",
        parameters_schema={"path": {"type": "string"}},
        required_parameters=["path"],
        risk_level=RiskLevel.MEDIUM,
        call_fn=fake_call,
    )

    result = tool.execute({"path": "Welcome.md", "lang": "tr"})

    assert captured == {"path": "Welcome.md"}
    assert result == "ok"


def test_mcp_tool_execute_truncates_long_result_for_tts():
    long_text = "x" * 1000

    tool = MCPTool(
        name="mcp_filesystem_read_text_file",
        description="test",
        parameters_schema={},
        required_parameters=[],
        risk_level=RiskLevel.MEDIUM,
        call_fn=lambda arguments: long_text,
    )

    result = tool.execute({"path": "Welcome.md", "lang": "tr"})

    assert result != long_text
    assert "terminalde" in result.lower()


def test_mcp_tool_execute_returns_short_result_verbatim():
    tool = MCPTool(
        name="mcp_filesystem_list_directory",
        description="test",
        parameters_schema={},
        required_parameters=[],
        risk_level=RiskLevel.MEDIUM,
        call_fn=lambda arguments: "kisa sonuc",
    )

    assert tool.execute({"lang": "tr"}) == "kisa sonuc"


def test_mcp_tool_execute_handles_empty_result():
    tool = MCPTool(
        name="mcp_filesystem_search_files",
        description="test",
        parameters_schema={},
        required_parameters=[],
        risk_level=RiskLevel.MEDIUM,
        call_fn=lambda arguments: "",
    )

    result = tool.execute({"lang": "en"})

    assert "empty" in result.lower()


# --- _wrap_mcp_tool (allowlist + sema esleme, IO'suz) ---


def _server_config(**overrides) -> MCPServerConfig:
    defaults = dict(
        name="filesystem",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem"],
        default_risk_level=RiskLevel.MEDIUM,
        allowed_tools=None,
    )
    defaults.update(overrides)
    return MCPServerConfig(**defaults)


def _mcp_tool_schema(name: str) -> MCPToolSchema:
    return MCPToolSchema(
        name=name,
        description=f"MCP {name} tool",
        inputSchema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    )


def test_wrap_mcp_tool_maps_schema_and_risk():
    server = _server_config()
    wrapped = _wrap_mcp_tool(server, _mcp_tool_schema("read_text_file"), call_fn=lambda a: "x")

    assert wrapped is not None
    assert wrapped.name == "mcp_filesystem_read_text_file"
    assert wrapped.risk_level is RiskLevel.MEDIUM
    assert wrapped.parameters_schema == {"path": {"type": "string"}}
    assert wrapped.required_parameters == ["path"]


def test_wrap_mcp_tool_filters_by_allowed_tools():
    server = _server_config(allowed_tools=["read_text_file", "list_directory"])

    allowed = _wrap_mcp_tool(server, _mcp_tool_schema("read_text_file"), call_fn=lambda a: "x")
    denied = _wrap_mcp_tool(server, _mcp_tool_schema("write_file"), call_fn=lambda a: "x")

    assert allowed is not None
    assert denied is None


def test_wrap_mcp_tool_no_allowlist_exposes_everything():
    server = _server_config(allowed_tools=None)
    wrapped = _wrap_mcp_tool(server, _mcp_tool_schema("write_file"), call_fn=lambda a: "x")
    assert wrapped is not None


# --- load_mcp_servers_config (fail-soft) ---


def test_load_mcp_servers_config_missing_file_returns_empty(tmp_path):
    missing = tmp_path / "does_not_exist.yaml"
    assert load_mcp_servers_config(str(missing)) == []


def test_load_mcp_servers_config_no_servers_key_returns_empty(tmp_path):
    yaml_path = tmp_path / "mcp_servers.yaml"
    yaml_path.write_text("foo: bar\n", encoding="utf-8")
    assert load_mcp_servers_config(str(yaml_path)) == []


def test_load_mcp_servers_config_disabled_server_excluded(tmp_path):
    yaml_path = tmp_path / "mcp_servers.yaml"
    yaml_path.write_text(
        "servers:\n"
        "  - name: filesystem\n"
        "    command: npx\n"
        "    enabled: false\n",
        encoding="utf-8",
    )
    assert load_mcp_servers_config(str(yaml_path)) == []


def test_load_mcp_servers_config_low_risk_upgraded_to_medium(tmp_path):
    yaml_path = tmp_path / "mcp_servers.yaml"
    yaml_path.write_text(
        "servers:\n"
        "  - name: filesystem\n"
        "    command: npx\n"
        "    default_risk_level: low\n",
        encoding="utf-8",
    )
    servers = load_mcp_servers_config(str(yaml_path))
    assert len(servers) == 1
    assert servers[0].default_risk_level is RiskLevel.MEDIUM


def test_load_mcp_servers_config_happy_path(tmp_path):
    yaml_path = tmp_path / "mcp_servers.yaml"
    yaml_path.write_text(
        "servers:\n"
        "  - name: filesystem\n"
        "    command: npx\n"
        "    args: [\"-y\", \"@modelcontextprotocol/server-filesystem\", \"C:\\\\vault\"]\n"
        "    default_risk_level: medium\n"
        "    allowed_tools: [\"read_text_file\", \"list_directory\"]\n",
        encoding="utf-8",
    )
    servers = load_mcp_servers_config(str(yaml_path))

    assert len(servers) == 1
    server = servers[0]
    assert server.name == "filesystem"
    assert server.command == "npx"
    assert server.allowed_tools == ["read_text_file", "list_directory"]
    assert server.default_risk_level is RiskLevel.MEDIUM
