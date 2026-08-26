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

from typing import Any, Iterable, Optional

from src.jarvis.tools.base import Tool

# JSON-Schema "type" -> Python tipi. Su an her tool sadece "string" kullaniyor
# ama semayi genisletebilecek gelecekteki bir tool icin diger temel tipler de
# tanimli (bkz. validate_arguments).
_JSON_TYPE_TO_PYTHON: dict[str, Any] = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
}


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


def validate_arguments(tool: Tool, arguments: dict) -> Optional[dict]:
    """Router'in (Ollama tool-calling) urettigi argumanlari `tool.parameters_schema`'ya
    karsi dogrular - FAIL-CLOSED.

    security-reviewer bulgusu (Faz 3.3): kucuk yerel modellerin (llama3.1:8b)
    JSON-Schema'ya HER ZAMAN sikica uyacaginin garantisi yok - model bir string
    yerine bir liste/dict/sayi da dondurebilir. Boyle bir deger dogrudan
    `Intent.parameters`'a gecerse, core/app.py:_execute_tool'daki guardrail
    taramasi VE onay paneli (ikisi de sadece `isinstance(value, str)` degerleri
    gosterir/tarar) bu degeri SESSIZCE atlar, ama `tool.execute()` yine de tam,
    dogrulanmamis degeri alirdi - guvenlik kontrolu ile gercek calistirma
    arasinda bir tutarsizlik acardi.

    Davranis: semada TANIMLI OLMAYAN anahtarlar sessizce elenir (LLM'in
    urettigi fazladan/halusine edilmis alanlar tool.execute()'a hic ulasmaz);
    semada tanimli bir anahtarin degeri BEKLENEN TIPTE degilse TUM cagri
    reddedilir (`None` doner) - boylece Intent.parameters'a ulasan her deger,
    guardrail/onay panelinin zaten guvendigi tipte (bugun icin: str) olur.
    """
    validated: dict[str, Any] = {}
    for key, value in arguments.items():
        expected_schema = tool.parameters_schema.get(key)
        if expected_schema is None:
            continue  # semada olmayan anahtar - sessizce elenir
        expected_type = _JSON_TYPE_TO_PYTHON.get(expected_schema.get("type", "string"), str)
        if not isinstance(value, expected_type):
            return None
        validated[key] = value
    return validated
