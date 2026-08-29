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

from src.jarvis.adapters.agent_factory import ROUTER_MODEL_NAME, AgentFactory
from src.jarvis.adapters.tool_schema import build_ollama_tools, validate_arguments
from src.jarvis.core.console import status_spinner
from src.jarvis.core.language import detect_language
from src.jarvis.core.trace import traced
from src.jarvis.tools.registry import all_tools, get_tool

logger = logging.getLogger("jarvis.dispatcher")

IntentSource = Literal["rule", "llm"]

# Bilinmeyen/rule-eslesmeyen ve router'in hicbir arac secmedigi her sey buraya
# duser - Brain'in normal sohbet davranisiyla es anlamli.
DEFAULT_INTENT_NAME = "chat"

# Sesli/yazili "sistemi kapat" komutu - core/app.py:_handle_turn() bunu ACIKCA
# yakalayip stop_event'i set ediyor (bkz. o dosyadaki kullanim). Bir TOOL_REGISTRY
# girdisi DEGIL (execute() gerektiren bir Tool degil, dogrudan surec kontrolu) -
# bu yuzden dispatcher.py'de, HANDLERS/TOOL_REGISTRY'nin disinda, kendi basina
# bir sabit olarak tutuluyor.
SHUTDOWN_INTENT_NAME = "shutdown"

# SADECE basit, belirsizlik tasimayan komutlar fast-path'te kaliyor - geri
# kalan TUM araclar (list_files, create_note, read_notes, run_command,
# get_system_info, launch_app, media_*) artik asagidaki classify() ile Ollama
# native tool-calling'e (semantic router) devrediliyor. Regex'ler kelime siniri
# (\b) ile TR/EN karisik konusmada yanlis eslesmeyi azaltir.
#
# Dile gore AYRI pattern'ler tutuluyor: hangi alternatifin eslestigi, o kalibin
# dilini KESIN olarak veriyor - langdetect'in kisa metinlerde (orn. "saat kaç?")
# yanlis sonuc verebilmesinden (gercek testte TR sorgusu yanlislikla "en" olarak
# tespit edildi) cok daha guvenilir.
#
# `shutdown` BILINCLI OLARAK fast-path'te (semantic router'a/LLM'e HIC gitmiyor) -
# surecin tamamini kapatan bir komutun bir kucuk modelin "hangi araci sececegim"
# kararina bagli olmasi istenmiyor (get_time'in "belirsizlik tasimiyor" ilkesiyle
# ayni gerekce, ama burada bilincli-tasarim kadar guvenlik/guvenilirlik de var).
_RULES: dict[str, list[tuple[str, re.Pattern]]] = {
    "get_time": [
        ("tr", re.compile(r"\bsaat kaç\b", re.IGNORECASE)),
        ("en", re.compile(r"\bwhat time is it\b", re.IGNORECASE)),
    ],
    SHUTDOWN_INTENT_NAME: [
        ("tr", re.compile(r"\b(sistemi|kendini) kapat\b", re.IGNORECASE)),
        ("en", re.compile(r"\bshut\s?down\b", re.IGNORECASE)),
        ("en", re.compile(r"\bturn (yourself|the system) off\b", re.IGNORECASE)),
    ],
}

# Router'a "gercek bir arac uymuyor" secenegini ACIKCA sunan sentetik bir
# fonksiyon adi - TOOL_REGISTRY'ye (tools/registry.py) KESINLIKLE eklenmiyor,
# gercek bir Tool degil, sadece classify()'in Ollama'ya sundugu semaya ayrica
# eklenen bir "kacis yolu".
#
# NEDEN GEREKLI (kok neden dogrulandi - `ollama show llama3.1:8b --modelfile`;
# Faz 6.2'de router qwen2.5:3b'ye tasindi, ayni davranis canli test edildi -
# bkz. docs/ROADMAP.md Faz 6.2 dogrulama):
# `ollama.chat(..., tools=[...])` cagrildiginda, kucuk yerel modellerin Ollama
# sablonu kullanicinin SON turunu sunucu tarafinda, KOSULSUZ olarak "Given the
# following functions, please respond with a JSON for a function call..."
# seklinde yeniden yaziyor - bu sablonda "ya da hicbir fonksiyon cagirma" dalı
# YOK. Bu, uretimden
# hemen once, en yuksek-dikkat konumda oturuyor ve asagidaki _ROUTER_SYSTEM_PROMPT'un
# "arac yoksa HICBIR FONKSIYON cagirma" talimatini YAPISAL OLARAK eziyor - bu
# yuzden temperature dusurmek VE promptu "hicbir fonksiyon cagirma" ornekleriyle
# guclendirmek (ikisi de bu projede once denendi) ISE YARAMADI: bu bir orn ekleme/
# prompt-uyum sorunu degil, sunucu tarafi sablon davranisi. Cozum, modele bu
# zorunlu "her zaman fonksiyon cagir" cercevesiyle CALISAN somut, semaya-uygun
# BIR fonksiyon (bu sentinel) vermek - soyut bir "cagirma" talimatiyla savasmak
# yerine. (Ikincil etki: model "bir sey cagirmaya" zorlaninca TOOL_REGISTRY'deki
# ilk parametresiz aracı - read_notes - en dusuk efor secenegi olarak seciyordu;
# bu sentinel artik o rolu devraliyor.)
_NO_TOOL_FUNCTION_NAME = "no_tool_needed"

_NO_TOOL_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": _NO_TOOL_FUNCTION_NAME,
        "description": (
            "Call this when the user is just chatting, greeting, saying "
            "goodbye, asking a general question, or none of the other "
            "functions clearly match their request."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

# Faz 6.3: router semasina eklenen iki sentetik delegasyon sentinel'i -
# `_NO_TOOL_SCHEMA` ile ayni desen (gercek Tool DEGIL, TOOL_REGISTRY'de yok).
# classify() bunlari `Intent("delegate_complex" | "delegate_code", 0.7)`'ye eslar;
# core/app.py:_handle_turn() o intent'leri tool_agent dongusune / ClaudeCodeAdapter'a
# yonlendirir (bkz. docs/ROADMAP.md Faz 6.3, v2 SS2.6).
_DELEGATE_COMPLEX_FUNCTION_NAME = "delegate_complex_task"
_DELEGATE_CODE_FUNCTION_NAME = "delegate_code_task"
DELEGATE_COMPLEX_INTENT_NAME = "delegate_complex"
DELEGATE_CODE_INTENT_NAME = "delegate_code"

_DELEGATE_COMPLEX_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": _DELEGATE_COMPLEX_FUNCTION_NAME,
        "description": (
            "Call this when the request needs SEVERAL tools coordinated in "
            "sequence with reasoning between steps (e.g. 'check the system status "
            "and take a note about it', 'search for X then note the result'). "
            "Not for a single tool call or a plain question."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The full multi-step task, in the user's words."}
            },
            "required": ["task"],
        },
    },
}

_DELEGATE_CODE_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": _DELEGATE_CODE_FUNCTION_NAME,
        "description": (
            "Call this ONLY for heavy software work: writing or refactoring code, "
            "debugging a program, deep codebase analysis, reviewing a repo. This "
            "hands the task to the Claude Code CLI. Not for general questions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The coding/analysis task, in the user's words."}
            },
            "required": ["task"],
        },
    },
}

# 2026-08-29 (Cluster C + F-hafif): ~50 satirlik yogun prompt qwen2.5:3b'de
# tutarsiz uygulaniyordu (canli testte "sarkiyi devam ettir" -> run_command
# `taskmgr /restart` uydurmasi). Kisa, yapisal, ornek-agirlikli bir surumle
# degistirildi - kucuk router modeli boyle daha guvenilir. run_command
# halusinasyonuna ayrica KOD-SEVIYESI guard var (`_command_appears_in_transcript`,
# classify()).
_ROUTER_SYSTEM_PROMPT = (
    "You route a JARVIS voice command to ONE function.\n\n"
    "RULES:\n"
    "1. If exactly one function clearly matches what the user wants, call it once.\n"
    f"2. Otherwise call `{_NO_TOOL_FUNCTION_NAME}`: plain chat, greetings, "
    "questions, opinions, or unclear/garbled input. When unsure, choose this - "
    "a missed tool is cheap, a wrong one is not.\n"
    "3. `run_command` ONLY when the user literally dictates a shell command "
    "word-for-word. Merely mentioning a terminal/command/computer is NOT a "
    "command. Never invent a command, path, or URL.\n"
    "4. A specific song/artist by name -> `search_music` (never `run_command`).\n"
    f"5. If the request has SEVERAL ordered steps (\"... and then ...\", "
    f"\"once ... sonra\", \"ve sonra ...\") or one step's input depends on "
    f"another's result -> `{_DELEGATE_COMPLEX_FUNCTION_NAME}` with the WHOLE "
    "request as `task`. Do not just pick the first step.\n"
    f"6. Heavy software work (writing/refactoring/debugging code, deep repo "
    f"analysis) -> `{_DELEGATE_CODE_FUNCTION_NAME}`.\n\n"
    "EXAMPLES:\n"
    f'- "Merhaba" / "how are you" / "tesekkurler" -> {_NO_TOOL_FUNCTION_NAME}\n'
    f'- "Jarvis, wake up" / "adim Tony" -> {_NO_TOOL_FUNCTION_NAME}\n'
    f'- "onayini bekliyorum, terminale bak" -> {_NO_TOOL_FUNCTION_NAME} '
    "(mentions a terminal, dictates nothing)\n"
    '- "sesi ac" / "sesi yukselt" / "louder" -> media_volume_up '
    '(| "sesi biraz ac" -> media_volume_up amount=biraz | "sesi cok ac" -> amount=cok)\n'
    '- "sesi kis" / "sesi dusur" / "quieter" -> media_volume_down\n'
    '- "sesi 84 yap" / "sesi %50 yap" / "set volume to 30" -> set_volume level=84\n'
    '- "siradaki sarki" / "siradaki sarkiya gec" / "next track" / "skip" -> media_next_track\n'
    '- "onceki sarki" / "geri git" / "previous track" -> media_previous_track\n'
    '- "muzigi durdur" / "duraklat" / "pause" / "sarkiyi devam ettir" -> media_play_pause '
    "(NOT run_command - nothing dictated)\n"
    '- "Iron Man cal" / "play Bohemian Rhapsody" -> search_music (query = the song)\n'
    '- "not al: sut al" -> create_note   |   "alisveris listesi basligiyla not al: sut, ekmek" '
    '-> create_note title="alisveris listesi" content="sut, ekmek"\n'
    '- "notlarimi oku" -> read_notes   |   "alisveris listesi notunu oku" -> read_notes title="alisveris listesi"\n'
    '- "notlarimi listele" / "hangi notlarim var" -> list_notes\n'
    '- "alisveris listesine yumurta ekle" -> append_to_note title="alisveris listesi" content="yumurta"\n'
    '- "alisveris listesi notunu ac" / "open my X note" -> open_note title="alisveris listesi"\n'
    '- "A ve B notlarini C\'de birlestir" -> merge_notes sources="A, B" target="C"\n'
    '- "sistem durumu" / "CPU kullanimi" -> get_system_info\n'
    '- "run dir" / "calistir: git status" -> run_command\n'
    '- "yeni bir proje olustur: blog" / "create a project called api" -> create_project (project_name = the name)\n'
    f'- "sistem durumuna bak, sonra bir not al" -> {_DELEGATE_COMPLEX_FUNCTION_NAME}\n'
    f'- "hava durumunu arastir ve not dus" -> {_DELEGATE_COMPLEX_FUNCTION_NAME}\n'
    f'- "dispatcher.py\'yi refactor et" -> {_DELEGATE_CODE_FUNCTION_NAME}\n'
)


def _command_appears_in_transcript(command: str, transcript: str) -> bool:
    """C2 (2026-08-29): router'in UYDURDUGU bir `run_command` cagrisini ele.

    Dikte edilen komutun ILK token'i (calistirilabilir - `taskmgr`, `dir`,
    `git`...) transkriptte bir KELIME olarak gecmiyorsa, kullanici boyle bir
    komut soylememis demektir. Canli testte "Jarvis sarkiyi devam ettir" ->
    router `run_command: taskmgr /restart` uretti ve HIGH onay kapisina kadar
    gitti. Bu, o kapinin USTUNE ikinci bir savunma katmani (defense-in-depth).
    """
    stripped = command.strip()
    if not stripped:
        return False
    first_token = stripped.split()[0].lower()
    words = set(re.findall(r"[\w\-./\\]+", transcript.lower()))
    return first_token in words


def _selection_confidence(tool, validated_arguments: dict) -> float:
    """C3 (2026-08-29): hardcoded 0.9 yerine kaba ama GERCEK bir sinyal - onay
    panelindeki "guven: 0.90" her kararda ayni cikmasin. Tum required
    parametreler dolu geldiyse 0.8; validate gecti ama bir required bos/eksikse
    0.6 (router argumani yakalayamamis, daha az emin)."""
    required = getattr(tool, "required_parameters", None) or []
    if all(str(validated_arguments.get(key, "")).strip() for key in required):
        return 0.8
    return 0.6


class Intent(BaseModel):
    """Bir kullanici transkriptinin siniflandirma sonucu."""

    name: str
    confidence: float = Field(ge=0.0, le=1.0)
    parameters: dict = Field(default_factory=dict)
    source: IntentSource


class Dispatcher:
    """Hibrit intent siniflandirici: once `_RULES`'a (fast-path) bakar, eslesme
    yoksa ayri bir mini router modeline (AgentFactory.create("router"), Faz 6.2 -
    su an qwen2.5:3b) TOOL_REGISTRY'deki araclardan birini secmesi icin native
    tool-calling ile sorar. Kucuk/hizli router modeli, sohbet turlarindaki
    cift-8B cagri gecikmesini azaltir (bkz. docs/mimari-genel-bakis.md SS20 madde 1).
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

        Maliyet (Faz 6.2 ile azaltildi): rule-eslesmeyen her turda router icin
        bir LLM cagrisi yapiliyor; eslesme yoksa Brain'e (ikinci cagri)
        dusuluyor. Router artik ayri, kucuk bir model (`qwen2.5:3b`, ~1 GB)
        oldugu icin bu ilk cagri cok daha ucuz - eski "router da 8B" durumundaki
        cift-8B gecikmesi cozuldu (bkz. docs/mimari-genel-bakis.md SS20 madde 1).
        """
        rule_match = self.match_rule(text)
        if rule_match is not None:
            return rule_match

        logger.info("Dispatcher: kural eslesmedi, semantic router'a dusuluyor.")
        # all_tools(): yerel TOOL_REGISTRY + MCP-kesfedilen araclarin birlesik
        # view'i (bkz. tools/registry.py, docs/ARCHITECTURE.md SS9.2) - TOOL_REGISTRY'nin
        # KENDISI degismiyor, sadece Router'a sunulan sema genisliyor.
        tools_schema = build_ollama_tools(all_tools().values()) + [
            _NO_TOOL_SCHEMA,
            _DELEGATE_COMPLEX_SCHEMA,
            _DELEGATE_CODE_SCHEMA,
        ]
        router = AgentFactory.create("router")
        context = [{"role": "system", "content": _ROUTER_SYSTEM_PROMPT}]

        # Faz 6.9: router çağrısını izle (çift-çağrı gecikmesinin ilk yarısı).
        with status_spinner("Yönlendiriliyor..."), traced(
            "router", model=ROUTER_MODEL_NAME, input_summary=text
        ):
            response = router.call_tools(text, tools=tools_schema, context=context)

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

        if call.name == _NO_TOOL_FUNCTION_NAME:
            # Beklenen, DOGRU sonuc (bkz. _NO_TOOL_FUNCTION_NAME'in dosya-ustu
            # notu) - normal bir sohbet turunde SIK SIK tetiklenecek, bu yuzden
            # warning DEGIL, info seviyesinde loglaniyor (asagidaki "bilinmeyen
            # arac" warning'inden bilincli olarak ayri tutuluyor).
            logger.info(
                "Dispatcher: router '%s' secti (duz sohbet/genel soru) -> chat.",
                _NO_TOOL_FUNCTION_NAME,
            )
            return Intent(name=DEFAULT_INTENT_NAME, confidence=0.6, source="llm")

        # Faz 6.3 delegasyon sentinel'leri (gercek Tool degil, get_tool() None doner)
        # - _NO_TOOL kontrolunden SONRA, get_tool()'dan ONCE. `task` argumani
        # router'dan HAM geliyor (validate_arguments YOK - Tool semasi yok); str'e
        # zorlaniyor, bos/eksikse ham girdi metnine dusuluyor. _handle_turn'de
        # ayrica _INPUT_GUARDRAIL'den geciyor.
        if call.name == _DELEGATE_COMPLEX_FUNCTION_NAME:
            task = str(call.arguments.get("task") or text).strip()
            logger.info("Dispatcher: router delegate_complex_task secti.")
            return Intent(
                name=DELEGATE_COMPLEX_INTENT_NAME,
                confidence=0.7,
                source="llm",
                parameters={"task": task, "lang": detect_language(text)},
            )
        if call.name == _DELEGATE_CODE_FUNCTION_NAME:
            task = str(call.arguments.get("task") or text).strip()
            logger.info("Dispatcher: router delegate_code_task secti.")
            return Intent(
                name=DELEGATE_CODE_INTENT_NAME,
                confidence=0.7,
                source="llm",
                parameters={"task": task, "lang": detect_language(text)},
            )

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

        # C2 (2026-08-29): run_command halusinasyon guard'i - router'in urettigi
        # komut transkriptte fiilen dikte edilmediyse reddet (bkz.
        # _command_appears_in_transcript).
        if call.name == "run_command" and not _command_appears_in_transcript(
            str(validated_arguments.get("command", "")), text
        ):
            logger.warning(
                "Dispatcher: run_command reddedildi - komut %r transkriptte (%r) "
                "dikte edilmemis (router uydurmasi) -> chat.",
                validated_arguments.get("command"),
                text,
            )
            return Intent(name=DEFAULT_INTENT_NAME, confidence=0.3, source="llm")

        lang = detect_language(text)
        parameters = {**validated_arguments, "lang": lang}
        confidence = _selection_confidence(tool, validated_arguments)
        logger.info("Dispatcher: router aracı secti (%s, guven=%.2f).", call.name, confidence)
        return Intent(name=call.name, confidence=confidence, source="llm", parameters=parameters)
