"""Intent yonlendirici - once rule-based (regex/anahtar kelime) fast-path, eslesme
yoksa Ollama native tool-calling ile semantic router (bkz. docs/ROADMAP.md Faz 3.3).

Amac, her kullanici transkriptini bir Intent'e (isim + guven skoru + parametreler)
cevirmek; gercek intent->fonksiyon eslemesi core.handlers/tools.registry'de, canli
dongudeki kullanimi core.app'te.
"""

import logging
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

from src.jarvis.adapters.agent_factory import AgentFactory
from src.jarvis.adapters.tool_schema import build_ollama_tools, validate_arguments
from src.jarvis.core.console import status_spinner
from src.jarvis.core.language import detect_language
from src.jarvis.tools.registry import all_tools, get_tool

logger = logging.getLogger("jarvis.dispatcher")

IntentSource = Literal["rule", "llm"]

# Bilinmeyen/rule-eslesmeyen ve router'in hicbir arac secmedigi her sey buraya
# duser - Brain'in normal sohbet davranisiyla es anlamli.
DEFAULT_INTENT_NAME = "chat"

# SADECE basit, belirsizlik tasimayan TEK bir komut fast-path'te kaliyor - geri
# kalan TUM araclar (list_files, create_note, read_notes, run_command,
# get_system_info, launch_app, media_*) artik asagidaki classify() ile Ollama
# native tool-calling'e (semantic router) devrediliyor. Regex'ler kelime siniri
# (\b) ile TR/EN karisik konusmada yanlis eslesmeyi azaltir.
#
# Dile gore AYRI pattern'ler tutuluyor: hangi alternatifin eslestigi, o kalibin
# dilini KESIN olarak veriyor - langdetect'in kisa metinlerde (orn. "saat kaç?")
# yanlis sonuc verebilmesinden (gercek testte TR sorgusu yanlislikla "en" olarak
# tespit edildi) cok daha guvenilir.
_RULES: dict[str, list[tuple[str, re.Pattern]]] = {
    "get_time": [
        ("tr", re.compile(r"\bsaat kaç\b", re.IGNORECASE)),
        ("en", re.compile(r"\bwhat time is it\b", re.IGNORECASE)),
    ],
}

_ROUTER_SYSTEM_PROMPT = (
    "You are JARVIS's tool-routing module. Look at the user's spoken command: if "
    "one of the provided functions clearly matches their intent, call ONLY that "
    "function, exactly once. If the user is just chatting, asking a general "
    "question, or no function clearly matches their intent, do NOT call any "
    "function.\n\n"
    # search_music (Faz 3.3 gercek kullanim testi bulgusu): kucuk yerel modeller
    # "play <song>" gibi istekler icin belirli bir arac varken bile run_command'a
    # kacip URL/dosya yolu UYDURUYORDU (orn. var olmayan bir YouTube video ID'si
    # veya olmayan bir kurulum yolu). Bu iki kural, modelin kendi "dunya
    # bilgisine" guvenip halusinasyon uretmesini ACIKCA yasaklar.
    "If the user asks to play, search for, or listen to a SPECIFIC song or "
    "artist by name, you MUST call search_music with that song/artist as the "
    "query - NEVER call run_command for this, even if you think you know a "
    "file path or URL for it.\n"
    "NEVER invent a file path, program location, or URL for run_command's "
    "command argument. Only use run_command for a command the user explicitly "
    "and literally dictated word-for-word. If you are not certain of the exact "
    "command, do not call any function."
)


class Intent(BaseModel):
    """Bir kullanici transkriptinin siniflandirma sonucu."""

    name: str
    confidence: float = Field(ge=0.0, le=1.0)
    parameters: dict = Field(default_factory=dict)
    source: IntentSource


class Dispatcher:
    """Hibrit intent siniflandirici: once `_RULES`'a (fast-path) bakar, eslesme
    yoksa Orkestrator'e (AgentFactory.create("orchestrator")) TOOL_REGISTRY'deki
    araclardan birini secmesi icin native tool-calling ile sorar.
    """

    def match_rule(self, text: str) -> Optional[Intent]:
        """Sadece `_RULES`'a bakar, LLM'e HIC gitmez - eslesme yoksa None doner.

        `parameters["lang"]`'a, eslesen pattern'in KENDI dili konur (bkz.
        `_RULES`'un ustundeki not) - langdetect'e degil, hangi dil-alternatifinin
        eslestigine guveniliyor. Handler'lar (core/handlers.py) Brain'i hic
        devreye sokmadan cevap uretiyor, yani SYSTEM_PROMPT'un "kullanicinin
        diliyle yanit ver" kuralindan faydalanamiyorlar - bu parametre olmadan
        eski cift-dilli sablon (bkz. eski _handle_get_time) TEK bir XTTS "lang"
        bayragiyla okunuyordu, metnin yarisi hep yanlis fonetikle cikiyordu.
        """
        for name, variants in _RULES.items():
            for lang, pattern in variants:
                match = pattern.search(text)
                if match:
                    logger.info("Dispatcher: kural eslesti (%s, dil=%s).", name, lang)
                    parameters: dict = {"lang": lang}
                    content = match.groupdict().get("content")
                    if content:
                        parameters["content"] = content.strip()
                    return Intent(
                        name=name, confidence=1.0, source="rule", parameters=parameters
                    )
        return None

    def classify(self, text: str) -> Intent:
        """Fast-path regex + semantic router (Ollama native tool-calling) hibriti.

        Bilinen maliyet (kabul edilen trade-off): rule-eslesmeyen her turda
        artik router icin bir LLM cagrisi yapiliyor - eslesme yoksa Brain'e
        (ikinci bir LLM cagrisi) dusuluyor. Bu, eski "sadece match_rule, hic
        LLM yok" davranisina kiyasla bir gecikme maliyeti (bkz. docs/ROADMAP.md
        "gelecek iyilestirme": router+chat'i tek cagriya birlestirme veya daha
        kucuk/hizli bir router modeli).
        """
        rule_match = self.match_rule(text)
        if rule_match is not None:
            return rule_match

        logger.info("Dispatcher: kural eslesmedi, semantic router'a dusuluyor.")
        # all_tools(): yerel TOOL_REGISTRY + MCP-kesfedilen araclarin birlesik
        # view'i (bkz. tools/registry.py, docs/ARCHITECTURE.md SS9.2) - TOOL_REGISTRY'nin
        # KENDISI degismiyor, sadece Router'a sunulan sema genisliyor.
        tools_schema = build_ollama_tools(all_tools().values())
        orchestrator = AgentFactory.create("orchestrator")
        context = [{"role": "system", "content": _ROUTER_SYSTEM_PROMPT}]

        with status_spinner("Yönlendiriliyor..."):
            response = orchestrator.call_tools(text, tools=tools_schema, context=context)

        # /debug (core/cli_commands.py) icin: router'in HAM cevabi - varsayilan
        # INFO seviyesinde gorunmez, sadece log seviyesi DEBUG'a cekildiginde.
        logger.debug(
            "Router ham yanıtı: tool_calls=%r content=%r", response.tool_calls, response.content
        )

        if not response.tool_calls:
            logger.info("Dispatcher: router hicbir arac secmedi -> chat.")
            return Intent(name=DEFAULT_INTENT_NAME, confidence=0.4, source="llm")

        if len(response.tool_calls) > 1:
            logger.warning(
                "Dispatcher: router birden fazla arac secti (%d), ilki kullaniliyor.",
                len(response.tool_calls),
            )
        call = response.tool_calls[0]

        tool = get_tool(call.name)
        if tool is None:
            logger.warning("Dispatcher: router bilinmeyen bir arac secti: %r -> chat.", call.name)
            return Intent(name=DEFAULT_INTENT_NAME, confidence=0.3, source="llm")

        # Fail-closed semantik dogrulama (bkz. adapters/tool_schema.py:validate_arguments
        # docstring'i, security-reviewer bulgusu): argumanlar semaya uymuyorsa
        # (beklenmeyen tip) tum cagri reddedilir - guardrail/onay panelinin
        # sessizce atlayabilecegi dogrulanmamis bir deger asla Intent.parameters'a
        # ulasmaz.
        validated_arguments = validate_arguments(tool, call.arguments)
        if validated_arguments is None:
            logger.warning(
                "Dispatcher: router argumanlari semaya uymuyor (%s: %r) -> chat.",
                call.name,
                call.arguments,
            )
            return Intent(name=DEFAULT_INTENT_NAME, confidence=0.3, source="llm")

        lang = detect_language(text)
        parameters = {**validated_arguments, "lang": lang}
        logger.info("Dispatcher: router aracı secti (%s).", call.name)
        return Intent(name=call.name, confidence=0.9, source="llm", parameters=parameters)
