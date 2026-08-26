"""Tool -> Ollama function-calling schema donusumu.

`tools/base.py:Tool` provider-agnostic, saf JSON-Schema metadata tasir
(`parameters_schema`/`required_parameters`) - Ollama'nin (veya baska bir
saglayicinin) bekledigi tam "tel formatina" (`{"type":"function","function":
{...}}`) sarmalamak, Ollama'ya bagimli kodun zaten yasadigi `adapters/`
paketinin isi (bkz. agent_factory.py ile simetrik konum, SRP).

Merkezi bir "hangi tool hangi semaya sahip" esleme dict'i BILINCLI OLARAK
tutulmuyor - bu, TOOL_REGISTRY/_RULES arasinda zaten yasanan (ve
test_registry_keys_match_tool_names ile zar zor tutulan) drift riskini bir
kat daha artirirdi. Bunun yerine her Tool kendi semasini tasir, burada
sadece donusturuluyor.
"""

from typing import Iterable

from src.jarvis.tools.base import Tool


def build_function_schema(tool: Tool) -> dict:
    """Tek bir Tool'u Ollama/OpenAI-stili bir function-calling tanimina cevirir."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": {
                "type": "object",
                "properties": tool.parameters_schema,
                "required": tool.required_parameters,
            },
        },
    }


def build_ollama_tools(tools: Iterable[Tool]) -> list[dict]:
    """Bir Tool koleksiyonunu, `ollama.chat(..., tools=...)`'a verilecek listeye cevirir."""
    return [build_function_schema(tool) for tool in tools]
