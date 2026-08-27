import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Callable, Iterator, Optional

from src.jarvis.adapters.agent_factory import AgentFactory
from src.jarvis.adapters.tool_schema import build_ollama_tools, validate_arguments
from src.jarvis.brain.llm import SYSTEM_PROMPT, think_and_respond_stream
from src.jarvis.core import api, hud_bus
from src.jarvis.core.cli_commands import handle_cli_command, is_cli_command
from src.jarvis.core.console import (
    print_agent,
    print_approval_panel,
    print_approval_prompt,
    print_router_decision,
    print_system,
    status_spinner,
)
from src.jarvis.core.dispatcher import (
    DEFAULT_INTENT_NAME,
    DELEGATE_CODE_INTENT_NAME,
    DELEGATE_COMPLEX_INTENT_NAME,
    SHUTDOWN_INTENT_NAME,
    Dispatcher,
    Intent,
    _NO_TOOL_FUNCTION_NAME,
    _NO_TOOL_SCHEMA,
)
from src.jarvis.core.guardrail.base import GuardrailChain
from src.jarvis.core.guardrail.input_checks import InputInjectionCheck
from src.jarvis.core.guardrail.output_checks import OutputSafetyCheck
from src.jarvis.core.handlers import HANDLERS
from src.jarvis.core.input_hub import InputEvent, InputHub
from src.jarvis.core.language import detect_language
from src.jarvis.core.risk import evaluate_approval_answer, request_approval, requires_approval
from src.jarvis.mouth.tts import speak
from src.jarvis.tools.base import Tool
from src.jarvis.tools.registry import all_tools, get_tool

logger = logging.getLogger("jarvis.core.app")

_INPUT_GUARDRAIL = GuardrailChain([InputInjectionCheck()])
_OUTPUT_GUARDRAIL = GuardrailChain([OutputSafetyCheck()])
_DISPATCHER = Dispatcher()

# Girdi guardrail'i reddettiginde soylenecek, TEK dilde mesaj (kullanicinin girdisinden
# tespit edilen dile gore) - eskiden iki dili tek cumlede birlestirip TEK bir XTTS lang
# bayragiyla okutuyorduk, bu da metnin yarisini hep yanlis fonetikle okutuyordu (bkz.
# core/handlers.py'nin ayni duzeltmesi, docs/ROADMAP.md Faz 3-oncesi bug-fix notu).
_INPUT_REJECTED_MESSAGES = {
    "tr": "Bu isteği işleyemiyorum.",
    "en": "I can't process that request.",
}

_APPROVAL_PENDING_MESSAGES = {
    "tr": "Onayınızı bekliyorum, terminale bakın.",
    "en": "I need your approval, please check the terminal.",
}
_APPROVAL_DENIED_MESSAGES = {
    "tr": "Anlaşıldı, iptal ettim.",
    "en": "Understood, I've cancelled it.",
}
_UNSAFE_COMMAND_MESSAGES = {
    "tr": "Bu komut güvenlik kontrolüne takıldı, çalıştırmayacağım.",
    "en": "That command was blocked by the safety check, I won't run it.",
}
_TOOL_FAILED_MESSAGES = {
    "tr": "Aracı çalıştırırken bir hata oluştu.",
    "en": "Something went wrong while running that tool.",
}
_TOOL_TIMEOUT_MESSAGES = {
    "tr": "Bu işlem çok uzun sürdü, durdurdum.",
    "en": "That action took too long, so I stopped it.",
}
_SHUTDOWN_MESSAGES = {
    "tr": "Anlaşıldı, kapanıyorum.",
    "en": "Understood, shutting down.",
}
_DELEGATE_CODE_NOTICE_MESSAGES = {
    "tr": "Bunu Claude Code'a devrediyorum, biraz sürebilir.",
    "en": "I'm handing this to Claude Code, it may take a moment.",
}
_DELEGATE_FAILED_MESSAGES = {
    "tr": "Görevi tamamlayamadım.",
    "en": "I couldn't complete that task.",
}

# Faz 6.3: delegate_complex -> tool_agent (hermes3:8b) ile SINIRLI cok adimli
# dongu. Her adim mevcut _execute_tool'dan geciyor (onay + guardrail + timeout +
# HUD) - yeni guvenlik yuzeyi yok. ~3 adim: tam otonom plan->arac->degerlendir
# dongusu DEGIL (o Faz 4, bkz. docs/jarvis-mimari-v2-multiagent-entegrasyon.md SS10).
_MAX_DELEGATE_STEPS = 3
_TOOL_AGENT_SYSTEM_PROMPT = (
    "You are JARVIS's task executor. Break the user's task into tool steps. Call "
    "ONE tool at a time; you'll get its result, then decide the next step. When the "
    f"task is fully done, call `{_NO_TOOL_FUNCTION_NAME}` and reply with a single "
    "short spoken sentence summarising what you did (no markdown, no lists)."
)

# Bir tool cagrisi bu sureyi asarsa iptal edilir ve kullaniciya zaman-asimi
# mesaji donulur. Donmus/kotu niyetli bir arac (ozellikle ic timeout'u olmayan
# bir MCP cagrisi) run_jarvis() ana dongusunu suresiz bloklamamali - bkz. bu
# modulun docstring'i "tek bir bloklayici cagriyi kesemez" sinirlamasi. MCP'nin
# kendi ic timeout'u (adapters/mcp_client_adapter.py) daha kisadir; bu, ic
# timeout'u olmayan araclar icin son emniyet katmani. Testlerde monkeypatch'lenir.
_TOOL_EXEC_TIMEOUT_SECONDS = 30.0


def _localized(messages: dict[str, str], lang: str) -> str:
    return messages.get(lang, messages["en"])


def _execute_tool(
    tool: Tool,
    intent,
    stop_event: Optional[threading.Event],
    input_hub: Optional[InputHub] = None,
    pending: Optional[list[InputEvent]] = None,
    on_start: Optional[Callable[[], None]] = None,
    speaking_event: Optional[threading.Event] = None,
) -> str:
    """`_run_tool_pipeline()`'i JARVIS HUD (web-ui) icin "start"/"end" olay
    yayinlarina sarmalayan ince bir kabuk.

    NEDEN AYRI BIR FONKSIYON (gercek govdeye DOKUNMADAN): `_run_tool_pipeline()`
    (asagida) guvenlik-kritik kontrol akisi boyunca BIRDEN FAZLA `return`
    noktasina sahip (guardrail red, onay reddi, execute hatasi, cikti guardrail
    red, basari). O akisin icine tek tek `hud_bus.publish_tool("end", ...)`
    serpistirmek hem hataya acik (bir donus noktasi unutulabilir) hem de
    guvenlik mantigiyla HUD bildirim mantigini karistirir. Bunun yerine TEK
    bir `try/finally` ile HANGI donus yolundan cikilirse cikilsin tam olarak
    bir "start" ve bir "end" olayi garanti edilir - guvenlik akisi asagida
    hicbir sekilde degismedi.
    """
    visible_params = {
        key: str(value) for key, value in intent.parameters.items() if key != "lang" and value not in (None, "")
    }
    hud_bus.publish_tool("start", tool.name, params=visible_params)
    result: Optional[str] = None
    try:
        result = _run_tool_pipeline(
            tool, intent, stop_event, input_hub=input_hub, pending=pending, on_start=on_start, speaking_event=speaking_event
        )
        return result
    finally:
        hud_bus.publish_tool("end", tool.name, result=result)


def _prompt_for_approval(
    prompt: str,
    panel_name: str,
    panel_risk: str,
    panel_params: dict,
    lang: str,
    *,
    input_hub: Optional[InputHub] = None,
    pending: Optional[list[InputEvent]] = None,
    stop_event: Optional[threading.Event] = None,
    speaking_event: Optional[threading.Event] = None,
) -> bool:
    """Orta/uzeri riskli bir eylem icin zorunlu insan onayi (Zero-Trust).

    Kullanici ekrana bakmiyor olabilecegi icin once sesli uyarilir, sonra ekranda
    buyuk bir panelle TUM parametreler gosterilip terminalde bloklayici soru
    sorulur - kullanici (LLM'in urettigi) argumani kendi soylediginden farkli
    olsa bile GORUR. `_run_tool_pipeline` step (2)'sinden cikarildi; delegate_code
    dali da bunu kullaniyor (iki onay yolu drift etmesin)."""
    speak(
        _localized(_APPROVAL_PENDING_MESSAGES, lang),
        language=lang,
        stop_event=stop_event,
        speaking_event=speaking_event,
    )
    print_approval_panel(panel_name, panel_risk, panel_params)
    if input_hub is not None:
        print_approval_prompt(prompt)
        answer = input_hub.wait_for_text_answer(pending if pending is not None else [])
        if is_cli_command(answer):
            # security-reviewer bulgusu (DX): onay bekleniyorken yazilan bir
            # "/..." komutu fail-closed olarak "hayir" sayilir (guvenli yon) ama
            # sessizce yutulmamali - kullaniciya ne oldugunu soyluyoruz.
            print_system(
                "Onay beklenirken bir komut yazdınız, 'hayır' olarak sayıldı - "
                "onay sonrası tekrar deneyin.",
                level="warning",
            )
        return evaluate_approval_answer(answer)
    return request_approval(prompt)


def _run_tool_pipeline(
    tool: Tool,
    intent,
    stop_event: Optional[threading.Event],
    input_hub: Optional[InputHub] = None,
    pending: Optional[list[InputEvent]] = None,
    on_start: Optional[Callable[[], None]] = None,
    speaking_event: Optional[threading.Event] = None,
) -> str:
    """Bir tool'u risk kontrolu + insan onayindan gecirerek calistirir.

    Guvenlik karari BURADA, tek merkezde veriliyor - tool'un kendisine birakilmiyor
    (bkz. tools/base.py). Sira: (1) tehlikeli komut on-taramasi (girdi), (2) risk
    seviyesine gore [Y/N] onayi, (3) calistirma, (4) sonuc guardrail taramasi (cikti -
    MCP'nin dis/guvenilmeyen verisi icin eklendi, bkz. asagidaki (4) yorumu).

    `input_hub`/`pending` (hibrit girdi modu, bkz. core/input_hub.py): verilirse
    onay cevabi `core/risk.py:request_approval()`nin KENDI `console.input()`
    cagrisi yerine paylasilan girdi kuyrugundan okunur - iki thread'in (onay
    bekleyen ana thread + surekli input() donen metin thread'i) ayni anda
    stdin okumaya calismasini (tanimsiz bir yaris) onlemenin tek yolu bu
    (bkz. input_hub.py modul docstring'i). `input_hub=None` (varsayilan) eski
    davranisi korur - hibrit-disi/gelecekteki cagiranlar icin geriye donuk uyumlu.

    `speaking_event` - `run_jarvis()`'in olusturup `mouth/tts.py:speak()` ve
    `core/input_hub.py:InputHub`'a ORTAK gecirdigi ayni event; buradaki TEK
    kullanimi onay-bekleme anonsunun (`_APPROVAL_PENDING_MESSAGES`) `speak()`
    cagrisina AYNEN iletilmesi - boylece bu anons da mikrofonu diger tum
    `speak()` cagrilariyla ayni sekilde gecici susturur (bkz. `run_jarvis()`
    docstring'indeki `speaking_event` notu).

    `on_start` (security-reviewer bulgusu): tool calistirmaya baslamadan
    ONCE (onay paneli/istemi basilmadan once) cagrilir - run_jarvis() bunu
    "Jarvis dusunuyor..." spinner'ini durdurmak icin kullanir. Eskiden
    spinner sadece _handle_turn()'un YIELD ettigi ILK cumlede duruyordu,
    ama tool-calistiran turlarda tek yield onay TAMAMEN bittikten SONRA
    geliyordu - yani onay paneli/[Y/N] istemi, rich'in kendi kendini
    yenileyen Status/Live render'i AKTIFKEN basiliyordu (gorsel karisma/
    guvenlik-kritik onay isteminin gizlenme riski).

    GENELLESTIRME NOTU (Faz 3.3, semantic router): eskiden SADECE
    `intent.parameters["content"]` guardrail'den geciyordu (parametreler tek bir
    sabit "content" regex named-group'undan geliyordu). Artik router, tool-ozgu
    anlamli parametre adlari (`command`, `app_name`, `content`) uretiyor - tek bir
    sabit anahtara guvenmek yetersiz. Bu yuzden `lang` haric TUM parametreler
    taranip onay panelinde gosteriliyor (bkz. tools/terminal_tool.py modul
    docstring'indeki "GECIS TAMAMLANDI" notu - bu, o gecisin somut mitigasyonu).

    GUVENLIK NOTU (security-reviewer bulgusu, Faz 3.3): degerler `str()` ile
    donusturuluyor, `isinstance(value, str)` ile FILTRELENMIYOR - eski hali,
    router beklenmedik bir tipte (liste/dict/sayi) bir deger urettiginde o
    degeri SESSIZCE hem guardrail taramasindan hem onay panelinden atlatirdi
    (ama tool.execute() yine de tam/dogrulanmamis degeri alirdi). Asil
    savunma `Dispatcher.classify()`'daki `validate_arguments()` (fail-closed,
    bkz. adapters/tool_schema.py) - bu satirdaki `str()` ise "beklenmeyen bir
    deger buraya kadar sizarsa bile hicbir sey sessizce gizlenmez" seklinde
    ikinci bir savunma katmani.
    """
    if on_start is not None:
        on_start()

    lang = intent.parameters.get("lang", "en")
    risky_values = {
        key: str(value)
        for key, value in intent.parameters.items()
        if key != "lang" and value not in (None, "")
    }

    # (1) Risk tasiyabilecek TUM parametreleri, LLM ciktisi icin kullandigimiz
    # ayni guardrail'den geciriyoruz - kullaniciya onay bile sorulmadan bilinen
    # yikici kaliplar (rm -rf, format, DROP TABLE...) reddedilsin diye
    # (defense-in-depth: yanlislikla "Y"ye basma ihtimali bu kaliplar icin dogmuyor).
    for value in risky_values.values():
        safety = _OUTPUT_GUARDRAIL.run(value)
        if not safety.allowed:
            logger.warning("Tool girdisi guardrail'e takildi (%s): %s", tool.name, safety.reason)
            return _localized(_UNSAFE_COMMAND_MESSAGES, lang)

    # (2) Orta ve uzeri risk -> zorunlu insan onayi (bkz. _prompt_for_approval).
    if requires_approval(tool.risk_level):
        prompt = f"'{tool.name}' calistirilsin mi? (risk: {tool.risk_level.value})"
        approved = _prompt_for_approval(
            prompt,
            tool.name,
            tool.risk_level.value,
            risky_values,
            lang,
            input_hub=input_hub,
            pending=pending,
            stop_event=stop_event,
            speaking_event=speaking_event,
        )
        if not approved:
            return _localized(_APPROVAL_DENIED_MESSAGES, lang)

    # (3) Calistir - tek bir kotu tool cagrisi run_jarvis()'in dongusunu cokertmemeli
    # (_transcribe()/speak()'teki ayni izolasyon deseni). tool.execute() ayri bir
    # worker thread'de kosuluyor: ic timeout'u olmayan donmus bir arac (ornegin
    # bir MCP cagrisi) ana dongude _TOOL_EXEC_TIMEOUT_SECONDS'ten fazla bloklama
    # yapamasin. KABUL EDILEN SINIR: future.result(timeout) zaman asiminda calisan
    # thread'i durduramaz - kullaniciya hata donulur ama thread kendi bitene kadar
    # arka planda kalir (concurrent.futures'in yapisi; MCP'nin kendi asyncio
    # iptali daha guclu, bkz. adapters/mcp_client_adapter.py).
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"tool-{tool.name}")
    future = executor.submit(tool.execute, intent.parameters)
    try:
        result = future.result(timeout=_TOOL_EXEC_TIMEOUT_SECONDS)
    except FuturesTimeout:
        # FuturesTimeout, Exception'in alt sinifi - bu blok asagidaki genel
        # 'except Exception'dan ONCE gelmeli.
        logger.error(
            "Tool zaman asimina ugradi (%s, %.0fs)", tool.name, _TOOL_EXEC_TIMEOUT_SECONDS
        )
        executor.shutdown(wait=False, cancel_futures=True)
        return _localized(_TOOL_TIMEOUT_MESSAGES, lang)
    except Exception as exc:
        logger.error("Tool calistirilamadi (%s): %s", tool.name, exc)
        executor.shutdown(wait=False, cancel_futures=True)
        return _localized(_TOOL_FAILED_MESSAGES, lang)
    executor.shutdown(wait=False)

    # (4) MCP entegrasyonu (Faz 4.5, bkz. docs/ARCHITECTURE.md SS9.5) DONUS
    # degerini de guardrail'e sokuyor - eskiden SADECE girdi parametreleri
    # taraniyordu (yukaridaki (1)), cunku o zamana kadar HER tool'un donus
    # degeri kod-sabit/kullanicinin kendi yazdigi icerikti (guvenli). MCP
    # araclarinin donus degeri ise DIS/guvenilmeyen veridir (bir dosya/DB/repo
    # icerigi) - klasik indirect-prompt-injection/TTS uzerinden sosyal
    # muhendislik yuzeyi. Chat streaming yolunun zaten yaptigi per-cumle
    # taramayla (asagida _handle_turn) simetrik hale getiriliyor; yerel
    # araclar icin davranis degismiyor (donusleri zaten temiz).
    output_safety = _OUTPUT_GUARDRAIL.run(result)
    if not output_safety.allowed:
        logger.warning(
            "Tool ciktisi guardrail'e takildi (%s): %s", tool.name, output_safety.reason
        )
        return _localized(_UNSAFE_COMMAND_MESSAGES, lang)

    return result


def _run_delegate_complex(
    intent,
    stop_event: Optional[threading.Event],
    input_hub: Optional[InputHub],
    pending: Optional[list[InputEvent]],
    speaking_event: Optional[threading.Event],
    on_start: Optional[Callable[[], None]],
) -> Iterator[tuple[str, Optional[str]]]:
    """delegate_complex intent: tool_agent (hermes3:8b) ile SINIRLI (<= _MAX_DELEGATE_STEPS)
    cok adimli dongu. Her adimda ajan bir arac secer, secilen arac mevcut
    `_execute_tool` (onay + guardrail + timeout + HUD) ile calistirilir, sonuc
    ajana geri beslenir. Ajan `no_tool_needed` cagirinca (veya adim limiti
    dolunca) biter, ozet konusulur. Router-uretilmis `task` once _INPUT_GUARDRAIL'den
    geciyor."""
    lang = intent.parameters.get("lang", "en")
    task = str(intent.parameters.get("task") or "").strip()
    if on_start is not None:
        on_start()
    if not task or not _INPUT_GUARDRAIL.run(task).allowed:
        logger.warning("delegate_complex: gorev bos veya guardrail'e takildi.")
        yield _localized(_DELEGATE_FAILED_MESSAGES, lang), lang
        return

    agent = AgentFactory.create("tool_agent")
    schema = build_ollama_tools(all_tools().values()) + [_NO_TOOL_SCHEMA]
    messages: list[dict] = [{"role": "system", "content": _TOOL_AGENT_SYSTEM_PROMPT}]
    prompt = task
    last_result = ""

    for step in range(_MAX_DELEGATE_STEPS):
        resp = agent.call_tools(prompt, tools=schema, context=messages)
        messages.append({"role": "user", "content": prompt})
        if not resp.tool_calls or resp.tool_calls[0].name == _NO_TOOL_FUNCTION_NAME:
            summary = (resp.content or "").strip()
            yield summary or last_result or _localized(_DELEGATE_FAILED_MESSAGES, lang), lang
            return

        call = resp.tool_calls[0]
        tool = get_tool(call.name)
        validated = validate_arguments(tool, call.arguments) if tool is not None else None
        if tool is None or validated is None:
            logger.warning("delegate_complex adim %d: gecersiz arac cagrisi %r.", step, call.name)
            messages.append({"role": "assistant", "content": f"(skipped invalid call {call.name})"})
            prompt = "That step was invalid. Continue with a valid tool, or finish."
            continue

        step_intent = Intent(
            name=call.name, confidence=0.7, source="llm", parameters={**validated, "lang": lang}
        )
        last_result = _execute_tool(
            tool,
            step_intent,
            stop_event,
            input_hub=input_hub,
            pending=pending,
            speaking_event=speaking_event,
        )
        messages.append(
            {"role": "assistant", "content": f"[called {call.name}] result: {last_result}"}
        )
        prompt = "Continue with the next step, or finish if the task is done."

    # Adim limiti doldu, ajan kendini "bitti" ilan etmedi - son adimin sonucunu don.
    logger.info("delegate_complex: %d adim limiti doldu.", _MAX_DELEGATE_STEPS)
    yield last_result or _localized(_DELEGATE_FAILED_MESSAGES, lang), lang


def _run_delegate_code(
    intent,
    stop_event: Optional[threading.Event],
    input_hub: Optional[InputHub],
    pending: Optional[list[InputEvent]],
    speaking_event: Optional[threading.Event],
    on_start: Optional[Callable[[], None]] = None,
) -> Iterator[tuple[str, Optional[str]]]:
    """delegate_code intent: gorevi yerel `claude -p` (Claude Code CLI, salt-okuma)
    alt surecine devreder. Dis bir ajan + kod tabani erisimi oldugu icin ONAY
    kapisindan geciyor (HIGH). `claude -p` dosya DEGISTIRMEZ; sonuc (analiz/plan)
    sesli okunur, kullanici isterse ayrica uygular. Cagri ana dongude bloklar
    (bkz. ClaudeCodeAdapter docstring'i)."""
    lang = intent.parameters.get("lang", "en")
    task = str(intent.parameters.get("task") or "").strip()
    if on_start is not None:
        on_start()
    if not task or not _INPUT_GUARDRAIL.run(task).allowed:
        logger.warning("delegate_code: gorev bos veya guardrail'e takildi.")
        yield _localized(_DELEGATE_FAILED_MESSAGES, lang), lang
        return

    approved = _prompt_for_approval(
        "Claude Code'a devredilsin mi? (kod tabanını okur, değiştirmez)",
        "delegate_code",
        "high",
        {"task": task},
        lang,
        input_hub=input_hub,
        pending=pending,
        stop_event=stop_event,
        speaking_event=speaking_event,
    )
    if not approved:
        yield _localized(_APPROVAL_DENIED_MESSAGES, lang), lang
        return

    speak(
        _localized(_DELEGATE_CODE_NOTICE_MESSAGES, lang),
        language=lang,
        stop_event=stop_event,
        speaking_event=speaking_event,
    )
    agent = AgentFactory.create("deep_reasoning")
    # TTS dostu kalsin diye kisitlama ekleniyor - `claude -p` varsayilan olarak
    # markdown/kod blogu uretir, sesli okunamaz.
    tts_task = (
        f"{task}\n\nReply in at most 3 spoken sentences. No code blocks, no "
        "markdown, no lists - this will be read aloud."
    )
    result = agent.respond(tts_task)
    if not _OUTPUT_GUARDRAIL.run(result).allowed:
        logger.warning("delegate_code: Claude Code ciktisi guardrail'e takildi.")
        yield _localized(_UNSAFE_COMMAND_MESSAGES, lang), lang
        return
    yield result, lang


def _handle_turn(
    user_text: str,
    history: list[dict],
    stop_event: Optional[threading.Event] = None,
    input_hub: Optional[InputHub] = None,
    pending: Optional[list[InputEvent]] = None,
    on_tool_start: Optional[Callable[[], None]] = None,
    speaking_event: Optional[threading.Event] = None,
) -> Iterator[tuple[str, Optional[str]]]:
    """Bir kullanici turunu guardrail + dispatcher'dan gecirip (metin, dil) ciftleri uretir.

    Sira: (1) girdi guardrail'i - reddedilirse Brain'e hic gidilmez, history kirlenmez,
    TEK dilde (tespit edilen girdi diline gore) bir ret mesaji doner; (2) dispatcher
    (bkz. Dispatcher.classify: once LLM'e gitmeyen fast-path regex, sonra semantic
    router) - once risk-tasimayan HANDLERS (orn. get_time), sonra risk-kontrollu
    TOOL_REGISTRY (Faz 3 araclari, bkz. _execute_tool); ikisinde de Brain'e hic
    gidilmiyor; (3) aksi halde (intent.name == "chat") normal streaming sohbet -
    her cumle icin dil None donuyor (Brain SYSTEM_PROMPT sayesinde zaten girdi
    diliyle eslesiyor, speak()'in kendi auto-detect'i yeterli), ama once cikti
    guardrail'inden geciyor, reddedilen cumleler atlaniyor.

    `stop_event` verilirse, Brain'in streaming yanitini urettigi surece her cumle
    sonrasi kontrol edilir - kapatma istenirse kalan cumleler beklenmeden erken cikilir.
    """
    input_result = _INPUT_GUARDRAIL.run(user_text)
    if not input_result.allowed:
        lang = detect_language(user_text)
        message = _INPUT_REJECTED_MESSAGES.get(lang, _INPUT_REJECTED_MESSAGES["en"])
        yield message, lang if lang in _INPUT_REJECTED_MESSAGES else "en"
        return

    intent = _DISPATCHER.classify(user_text)
    if intent.name != DEFAULT_INTENT_NAME:
        # Router karari (source="llm") sadece gercekten bir arac secildiyse
        # gosterilir - "chat"e dusen her tur icin panel basmak duz sohbette
        # gurultu yaratirdi (bkz. core/console.py:print_router_decision).
        if intent.source == "llm":
            print_router_decision(intent.name, intent.confidence, intent.parameters)

        if intent.name == SHUTDOWN_INTENT_NAME:
            # Ctrl+C ile AYNI kapatma yolunu tetikliyor (bkz. run_jarvis()'in
            # `while not stop_event.is_set()` kosulu) - ama BURADA, buyuk bir
            # exception firlatmak yerine, mevcut cooperative stop_event
            # mekanizmasi kullanilarak: bu tur normal sekilde bitip (veda
            # mesaji soylenip) run_jarvis()'in dongusu bir SONRAKI iterasyonda
            # kendiliginden cikiyor.
            if stop_event is not None:
                stop_event.set()
            lang = intent.parameters.get("lang", "en")
            yield _localized(_SHUTDOWN_MESSAGES, lang), lang
            return

        if intent.name == DELEGATE_COMPLEX_INTENT_NAME:
            yield from _run_delegate_complex(
                intent, stop_event, input_hub, pending, speaking_event, on_tool_start
            )
            return

        if intent.name == DELEGATE_CODE_INTENT_NAME:
            yield from _run_delegate_code(
                intent, stop_event, input_hub, pending, speaking_event, on_tool_start
            )
            return

        handler = HANDLERS.get(intent.name)
        if handler is not None:
            text, lang = handler(intent)
            yield text, lang
            return

        tool = get_tool(intent.name)
        if tool is not None:
            result = _execute_tool(
                tool,
                intent,
                stop_event,
                input_hub=input_hub,
                pending=pending,
                on_start=on_tool_start,
                speaking_event=speaking_event,
            )
            yield result, intent.parameters.get("lang", "en")
            return

    for sentence in think_and_respond_stream(user_text, history):
        if stop_event is not None and stop_event.is_set():
            logger.info("Kapatma istendi, kalan yanit cumleleri atlaniyor.")
            break
        output_result = _OUTPUT_GUARDRAIL.run(sentence)
        if output_result.allowed:
            yield sentence, None
        # Reddedilen cumle sessizce atlanir - GuardrailChain zaten nedenini logluyor;
        # bir sohbetin ortasinda garip bir "engellendi" anonsu okumak yerine akici kalir.


def run_jarvis() -> None:
    """The main execution loop for the MVP pipeline (Ears + terminal -> guardrail/dispatcher -> Brain -> Mouth).

    HİBRİT GİRDİ (bkz. core/input_hub.py): mikrofon (wake-word + VAD) ve
    terminal metni ARTIK EŞ ZAMANLI dinleniyor - `InputHub` ikisini de kendi
    arka plan thread'inde çalıştırıp TEK bir sıralı kuyrukta birleştiriyor;
    bu fonksiyon (ana thread) sadece kuyruktan okur, ne mikrofona ne stdin'e
    doğrudan dokunur. Girdi kaynağı ne olursa olsun (ses/metin) pipeline'a
    aynı standart `(text, lang)` formatında girer - `/` ile başlayan metin
    turları TEK istisna: Guardrail/Dispatcher/Brain/TTS'e hiç uğramadan
    `core/cli_commands.py`'ye yönlendirilir (bkz. aşağıdaki döngü).

    Ctrl+C (KeyboardInterrupt) burada yakalanip `stop_event` set edilir; bu
    event `InputHub`'ın arka plan thread'lerine ve `speak()`'e (mouth/tts.py)
    geçiriliyor, hepsi kendi iç döngülerinde bunu periyodik kontrol edip erken
    çıkıyor - kapatma böylece dışarıdan bir exception'ın olur olmaz
    yayılmasına değil, iç bileşenlerin işbirliğine dayanıyor (graceful
    shutdown). ÖNEMLİ SINIRLAMA: bu, halihazırda çalışmakta olan TEK bir
    bloklayıcı çağrıyı (bir faster-whisper transkripsiyonu, bir Ollama
    isteği, bir XTTS inference chunk'ı, `input_hub.py`'nin metin thread'indeki
    tek bir `input()` çağrısı) yarıda kesemez - senkron çağrılar Python'un
    sinyal kontrol noktalarına dönene kadar beklenir; sadece bu çağrılar
    ARASINDAKI bekleme sürelerini anında kısaltır.

    MİKROFON KENDİ KENDİNİ TETİKLEME DÜZELTMESİ: `speaking_event` (aşağıda
    oluşturulur) hem `speak()`e hem `InputHub`'a geçirilir - `speak()` sesi
    çalarken bunu set eder, `InputHub`'ın mikrofon thread'i (`ears/listener.py:
    listen_loop()`) set'ken yeni bir wake-word/VAD tetiklemesi ARAMAZ. Bu
    olmadan (hibrit girdiden önceki senkron döngüde örtük olarak var olan bir
    korumaydı - `speak()` çalışırken `listen_loop()` generator'ı zaten askıda
    kalıyordu) düşük riskli bir araç (örn. `get_system_info`, onay gerektirmez)
    kendi sesli çıktısını mikrofondan duyup kendini sonsuza kadar yeniden
    tetikleyebiliyordu (projede akustik yankı bastırma/AEC yok).
    """
    # Boot ekrani (ASCII art + gercek Ears/Mouth/Brain yukleme spinner'lari) artik
    # main.py'de, bu fonksiyon cagrilmadan ONCE calisiyor (bkz. main.py) - o noktada
    # modeller zaten gercekten hazir, bu yuzden burada SADECE "dinlemeye basliyorum"
    # bildirimi kaliyor (eskiden buradaki "ONLINE" banner'i hicbir sey yuklenmeden
    # basiliyordu, bkz. docs/TODO.md/plan notlari - yaniltici oldugu icin kaldirildi).
    print_system("Jarvis dinlemeye hazır (mikrofon + 'SEN >>>' terminal girişi).", level="success")

    stop_event = threading.Event()
    # Jarvis konusurken (speak() suresince + kisa bir sonek) set kalir - InputHub'in
    # mikrofon thread'i bunu `mute_event` olarak okuyup yeni bir tetiklemeyi bu
    # sure boyunca ARAMAZ. Eklenme gerekcesi: mikrofon dinleme Faz 3.3'ten
    # (hibrit girdi, bkz. core/input_hub.py) itibaren ana thread'den bagimsiz,
    # her zaman acik bir arka plan thread'inde calisiyor - eski senkron dongude
    # speak() suresince generator askida oldugu icin mikrofon "kazara"
    # susturulmus oluyordu, bu ortuk koruma o zamandan beri yoktu. Onsuz, dusuk
    # riskli (onay gerektirmeyen) bir arac (orn. get_system_info) kendi sesli
    # ciktisini mikrofonundan tekrar duyup kendini sonsuza kadar yeniden
    # tetikleyebiliyordu (AEC yok) - bkz. mouth/tts.py:speak()'in speaking_event
    # notu ve ears/listener.py'nin mute_event notu.
    speaking_event = threading.Event()
    history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    hub = InputHub(stop_event, speaking_event)
    hub.start()
    # JARVIS HUD (web-ui): core/api.py'nin WebSocket'ten gelen yazili
    # komutlari bu hub'a iletebilmesinin TEK yolu - bkz. api.py:
    # register_input_hub() ve input_hub.py:submit_external_text() docstring'i.
    api.register_input_hub(hub)
    # Bir onay bekleme sirasinda gelen "voice" olaylari burada birikir (bkz.
    # InputHub.wait_for_text_answer()) - asagidaki dongu her turda ONCE
    # burayi bosaltir, kullanicinin o sirada soyledigi soz kaybolmaz.
    pending: list[InputEvent] = []

    try:
        # `stop_event.is_set()` KOSULU (eskiden `while True:`): "sistemi kapat"
        # (sesli/yazili, bkz. dispatcher.py:SHUTDOWN_INTENT_NAME) ve `/exit`
        # (core/cli_commands.py) BURADAN sonraki iterasyonda kendiliginden
        # cikabilsin diye - her iki yol da (KeyboardInterrupt'in KENDISI gibi)
        # sadece stop_event'i set ediyor, bu dongu ise bunu iteratıf olarak
        # kontrol ediyor (kapatma icin bir exception FIRLATILMASINA gerek yok).
        while not stop_event.is_set():
            event = pending.pop(0) if pending else hub.next_event()

            if event.source == "text" and is_cli_command(event.text):
                handle_cli_command(
                    event.text,
                    history=history,
                    stop_event=stop_event,
                    input_hub=hub,
                    pending=pending,
                    speaking_event=speaking_event,
                )
                continue

            print_agent("Siz" if event.source == "text" else "User", event.text)

            # JARVIS HUD (web-ui): NeuralCore'un "processing" gorsel durumu -
            # tool baslarsa/ilk cumle uretilirse zaten "speaking"/tool olaylari
            # bunun uzerine yazacak (son-yazan-kazanir, bkz. hud_bus.py).
            hud_bus.publish_state("processing")

            # Step 2 + 3: guardrail + dispatcher + Brain (streaming) -> Mouth, cumle cumle.
            # Spinner ilk cumle uretilene kadar acik kalir - sonraki cumleler icin
            # tekrar acilmiyor (Brain zaten stream halinde urettigi icin aralarda
            # gozle gorulur bir bekleme olmuyor, spinner'i her cumlede ac/kapa
            # gereksiz titreme yaratirdi).
            with status_spinner("Jarvis düşünüyor...") as spinner:
                first_sentence = True

                def _stop_spinner_once() -> None:
                    # nonlocal + idempotent: hem tool-calistiran yolun
                    # on_tool_start callback'inden (onay panelinden ONCE,
                    # bkz. _execute_tool docstring'i "on_start"), hem de
                    # asagidaki normal ilk-cumle yolundan cagrilabilir -
                    # ikisi de calissa spinner sadece BIR KEZ durur.
                    nonlocal first_sentence
                    if first_sentence:
                        spinner.stop()
                        first_sentence = False

                for sentence, lang in _handle_turn(
                    event.text,
                    history,
                    stop_event=stop_event,
                    input_hub=hub,
                    pending=pending,
                    on_tool_start=_stop_spinner_once,
                    speaking_event=speaking_event,
                ):
                    _stop_spinner_once()
                    print_agent("Jarvis", sentence)
                    speak(sentence, language=lang, stop_event=stop_event, speaking_event=speaking_event)
    except KeyboardInterrupt:
        print_system("Kapatma istendi (Ctrl+C) - güvenli şekilde kapatılıyor...", level="warning")
        stop_event.set()
    finally:
        print_system("Jarvis kapatıldı.", level="info")
