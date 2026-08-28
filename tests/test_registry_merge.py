"""tools/registry.py:all_tools() / get_tool() - UC kaynak birlesimi (Faz 6.4).

Oncelik: statik TOOL_REGISTRY > dinamik manifest > MCP. Ad cakismasinda statik
kazanir. Dinamik ve MCP katmanlari burada monkeypatch ile sahtelenip birlesim
mantigi izole test edilir (gercek npx/manifest gerekmez).

Calistirma: `python -m pytest tests/ -v` (repo kokunden).
"""

import logging

from src.jarvis.core.risk import RiskLevel
from src.jarvis.tools import registry as registry_module
from src.jarvis.tools.base import Tool


class _FakeTool(Tool):
    risk_level = RiskLevel.LOW

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"sahte {name}"

    def execute(self, params: dict) -> str:
        return self.name


def _patch_sources(monkeypatch, *, dynamic=None, mcp=None):
    monkeypatch.setattr(registry_module, "load_dynamic_tools", lambda: dict(dynamic or {}))
    monkeypatch.setattr(
        registry_module,
        "get_default_adapter",
        lambda: type("A", (), {"discover_tools": staticmethod(lambda: dict(mcp or {}))})(),
    )


def test_all_tools_merges_three_sources(monkeypatch):
    _patch_sources(
        monkeypatch,
        dynamic={"dyn_tool": _FakeTool("dyn_tool")},
        mcp={"mcp_x_y": _FakeTool("mcp_x_y")},
    )
    names = set(registry_module.all_tools())

    assert {"dyn_tool", "mcp_x_y"} <= names
    assert "get_system_info" in names  # statik hala orada


def test_static_wins_over_dynamic_name_collision(monkeypatch, caplog):
    _patch_sources(monkeypatch, dynamic={"get_system_info": _FakeTool("get_system_info")})

    with caplog.at_level(logging.WARNING, logger="jarvis.tools.registry"):
        merged = registry_module.all_tools()

    assert merged["get_system_info"] is registry_module.TOOL_REGISTRY["get_system_info"]
    assert any("cakisiyor" in r.message for r in caplog.records)


def test_dynamic_wins_over_mcp_for_shared_non_static_key(monkeypatch):
    _patch_sources(
        monkeypatch,
        dynamic={"shared": _FakeTool("from_dynamic")},
        mcp={"shared": _FakeTool("from_mcp")},
    )
    assert registry_module.all_tools()["shared"].execute({}) == "from_dynamic"


def test_get_tool_resolves_dynamic_tool(monkeypatch):
    _patch_sources(monkeypatch, dynamic={"dyn_tool": _FakeTool("dyn_tool")})
    assert registry_module.get_tool("dyn_tool").name == "dyn_tool"


def test_get_tool_prefers_static_over_dynamic(monkeypatch):
    _patch_sources(monkeypatch, dynamic={"get_system_info": _FakeTool("get_system_info")})
    assert registry_module.get_tool("get_system_info") is (
        registry_module.TOOL_REGISTRY["get_system_info"]
    )
