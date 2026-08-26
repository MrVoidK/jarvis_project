"""MCPTool - bir MCP sunucusunun TEK bir aracini yerel Tool sozlesmesine
sarmalayan wrapper (bkz. docs/ARCHITECTURE.md SS9.2, adapters/mcp_client_adapter.py).

Bilincli olarak MCPClientAdapter'a DOGRUDAN bagli degil - `call_fn` (senkron,
zaten async<->sync kopruden gecmis bir Callable[[dict], str]) disaridan
enjekte edilir (constructor injection). Bu, gercek bir MCP sunucusu/npx alt
sureci OLMADAN, sahte bir call_fn ile birim test edilebilir olmasini saglar
(bkz. tests/test_mcp_tool.py) - agents/base.py'deki Agent(ABC) ile
tools/base.py:Tool arasindaki ayni "arayuz uzerinden bagimsizlik" ilkesi.
"""

import logging
from typing import Callable

from src.jarvis.core.console import print_mcp_result
from src.jarvis.core.guardrail.base import GuardrailChain
from src.jarvis.core.guardrail.output_checks import OutputSafetyCheck
from src.jarvis.core.risk import RiskLevel
from src.jarvis.tools.base import Tool

logger = logging.getLogger("jarvis.tools.mcp_tool")

# security-reviewer bulgusu: eskiden guardrail core/app.py:_execute_tool()'da
# SADECE donen (zaten kirpilmis/yer-tutucu olabilen) degeri taniyordu - uzun
# bir sonuc icin taranan metin hep SABIT placeholder cumleydi, gercek icerik
# hicbir zaman guardrail'den gecmiyordu (docs/ARCHITECTURE.md SS9.5'in verdigi
# garantiyle celisiyordu). Bu yuzden RAW icerik, kirpma/konsola basmadan ONCE,
# burada taraniyor - app.py'nin kendi taramasi (yerel araclar icin) ayrica
# duruyor, MCP icin bu ikinci/erken kontrol asil savunma.
_RESULT_GUARDRAIL = GuardrailChain([OutputSafetyCheck()])

# TTS'e gidecek metin icin ust sinir - Tool.execute() sozlesmesi "kisa, tek
# cumle, markdown/liste/uzun cikti okunamaz" ister (bkz. tools/base.py), ama
# MCP dosya/DB icerigi cok daha uzun olabilir. Ekstra bir LLM ozetleme
# cagrisi BILINCLI OLARAK yok (gecikme/maliyet eklemez, MVP karari) - tam
# sonuc konsola yaziliyor (print_mcp_result), TTS'e sadece kirpilmis/kisa
# bir bildirim gidiyor.
_TTS_MAX_CHARS = 300

_TRUNCATED_MESSAGES = {
    "tr": "İçerik uzun, tam sonucu terminalde gösterdim.",
    "en": "The result is long, I've shown the full output in the terminal.",
}
_EMPTY_RESULT_MESSAGES = {
    "tr": "Bu araç boş bir sonuç döndürdü.",
    "en": "That tool returned an empty result.",
}
_BLOCKED_RESULT_MESSAGES = {
    "tr": "Bu aracın sonucu güvenlik kontrolüne takıldı, gösteremiyorum.",
    "en": "That tool's result was blocked by the safety check, I can't show it.",
}


def _localized(messages: dict[str, str], lang: str) -> str:
    return messages.get(lang, messages["en"])


def build_mcp_tool_name(server_name: str, mcp_tool_name: str) -> str:
    """`mcp_<sunucu>_<orijinal_ad>` uretir.

    Amac ikili: (1) yerel Tool'larla veya baska bir MCP sunucusunun ayni
    isimli araciyla CAKISMAYI onlemek (TOOL_REGISTRY'nin anahtar-cakismasi
    varsayimini bozmadan), (2) onay panelinde/loglarda aracin MCP KOKENLI
    oldugunu seffaf kilmak (bkz. core/console.py:print_approval_panel).
    """
    sanitized_server = "".join(ch if ch.isalnum() else "_" for ch in server_name.lower())
    sanitized_tool = "".join(ch if ch.isalnum() else "_" for ch in mcp_tool_name.lower())
    return f"mcp_{sanitized_server}_{sanitized_tool}"


class MCPTool(Tool):
    """Tek bir MCP aracinin Tool(ABC) sarmalayicisi."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters_schema: dict,
        required_parameters: list[str],
        risk_level: RiskLevel,
        call_fn: Callable[[dict], str],
    ) -> None:
        self.name = name
        self.description = description
        self.parameters_schema = parameters_schema
        self.required_parameters = required_parameters
        self.risk_level = risk_level
        self._call_fn = call_fn

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        # "lang" Jarvis'e ozgu, HER Intent.parameters'a Dispatcher.classify()
        # tarafindan eklenir (bkz. core/dispatcher.py:161) - MCP sunucusu
        # bunu bilmez/beklemez, gercek cagriya gitmeden filtrelenir.
        forwarded = {key: value for key, value in params.items() if key != "lang"}

        raw_result = self._call_fn(forwarded)

        if not raw_result:
            return _localized(_EMPTY_RESULT_MESSAGES, lang)

        # RAW icerik uzerinde, konsola basmadan/kirpmadan ONCE guardrail (bkz.
        # modul-seviyesi _RESULT_GUARDRAIL yorumu) - reddedilirse ne ekrana
        # ne TTS'e hicbir sey gitmez.
        safety = _RESULT_GUARDRAIL.run(raw_result)
        if not safety.allowed:
            logger.warning(
                "MCP araç sonucu güvenlik kontrolüne takıldı (%s): %s", self.name, safety.reason
            )
            return _localized(_BLOCKED_RESULT_MESSAGES, lang)

        print_mcp_result(self.name, raw_result)
        logger.info("MCP araç çalıştı (%s), sonuç uzunluğu=%d.", self.name, len(raw_result))

        if len(raw_result) <= _TTS_MAX_CHARS:
            return raw_result
        return _localized(_TRUNCATED_MESSAGES, lang)
