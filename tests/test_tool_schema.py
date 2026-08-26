"""adapters/tool_schema.py testleri - Tool -> Ollama function-calling JSON-Schema donusumu."""

from src.jarvis.adapters.tool_schema import build_function_schema, build_ollama_tools
from src.jarvis.core.risk import RiskLevel
from src.jarvis.tools.base import Tool


class _NoParamTool(Tool):
    name = "no_param_tool"
    description = "Parametresiz bir test araci."
    risk_level = RiskLevel.LOW

    def execute(self, params: dict) -> str:
        return "ok"


class _WithParamTool(Tool):
    name = "with_param_tool"
    description = "Parametreli bir test araci."
    risk_level = RiskLevel.MEDIUM
    parameters_schema = {"content": {"type": "string", "description": "Icerik."}}
    required_parameters = ["content"]

    def execute(self, params: dict) -> str:
        return "ok"


def test_build_function_schema_for_parameterless_tool():
    schema = build_function_schema(_NoParamTool())

    assert schema["type"] == "function"
    assert schema["function"]["name"] == "no_param_tool"
    assert schema["function"]["description"] == "Parametresiz bir test araci."
    assert schema["function"]["parameters"]["type"] == "object"
    assert schema["function"]["parameters"]["properties"] == {}
    assert schema["function"]["parameters"]["required"] == []


def test_build_function_schema_for_tool_with_parameters():
    schema = build_function_schema(_WithParamTool())

    assert schema["function"]["parameters"]["properties"] == {
        "content": {"type": "string", "description": "Icerik."}
    }
    assert schema["function"]["parameters"]["required"] == ["content"]


def test_build_ollama_tools_converts_a_collection():
    tools = build_ollama_tools([_NoParamTool(), _WithParamTool()])

    assert len(tools) == 2
    assert {tool["function"]["name"] for tool in tools} == {"no_param_tool", "with_param_tool"}
