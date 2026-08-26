import logging
import threading
from typing import Iterator, Optional

from src.jarvis.brain.llm import SYSTEM_PROMPT, think_and_respond_stream
from src.jarvis.core.console import (
    print_agent,
    print_approval_panel,
    print_router_decision,
    print_system,
    status_spinner,
)
from src.jarvis.core.dispatcher import DEFAULT_INTENT_NAME, Dispatcher
from src.jarvis.core.guardrail.base import GuardrailChain
from src.jarvis.core.guardrail.input_checks import InputInjectionCheck
from src.jarvis.core.guardrail.output_checks import OutputSafetyCheck
from src.jarvis.core.handlers import HANDLERS
from src.jarvis.core.language import detect_language
from src.jarvis.core.risk import request_approval, requires_approval
from src.jarvis.ears.listener import listen_loop
from src.jarvis.mouth.tts import speak
from src.jarvis.tools.base import Tool
from src.jarvis.tools.registry import TOOL_REGISTRY

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


def _localized(messages: dict[str, str], lang: str) -> str:
    return messages.get(lang, messages["en"])


def _execute_tool(tool: Tool, intent, stop_event: Optional[threading.Event]) -> str:
    """Bir tool'u risk kontrolu + insan onayindan gecirerek calistirir.

    Guvenlik karari BURADA, tek merkezde veriliyor - tool'un kendisine birakilmiyor
    (bkz. tools/base.py). Sira: (1) tehlikeli komut on-taramasi, (2) risk seviyesine
    gore [Y/N] onayi, (3) calistirma.

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

    # (2) Orta ve uzeri risk -> zorunlu insan onayi. Kullanici ekrana bakmiyor
    # olabilecegi icin once sesli uyariyoruz, sonra ekranda buyuk bir panelle
    # TUM parametreleri gosterip terminalde bloklayici soru soruyoruz - kullanici
    # router'in URETTIGI argumani, kendi soylediginden farkli olsa bile GORUR.
    if requires_approval(tool.risk_level):
        speak(_localized(_APPROVAL_PENDING_MESSAGES, lang), language=lang, stop_event=stop_event)
        print_approval_panel(tool.name, tool.risk_level.value, risky_values)
        prompt = f"'{tool.name}' calistirilsin mi? (risk: {tool.risk_level.value})"
        if not request_approval(prompt):
            return _localized(_APPROVAL_DENIED_MESSAGES, lang)

    # (3) Calistir - tek bir kotu tool cagrisi run_jarvis()'in dongusunu cokertmemeli
    # (_transcribe()/speak()'teki ayni izolasyon deseni).
    try:
        return tool.execute(intent.parameters)
    except Exception as exc:
        logger.error("Tool calistirilamadi (%s): %s", tool.name, exc)
        return _localized(_TOOL_FAILED_MESSAGES, lang)


def _handle_turn(
    user_text: str, history: list[dict], stop_event: Optional[threading.Event] = None
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

        handler = HANDLERS.get(intent.name)
        if handler is not None:
            text, lang = handler(intent)
            yield text, lang
            return

        tool = TOOL_REGISTRY.get(intent.name)
        if tool is not None:
            yield _execute_tool(tool, intent, stop_event), intent.parameters.get("lang", "en")
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
    """The main execution loop for the MVP pipeline (Ears -> guardrail/dispatcher -> Brain -> Mouth).

    Ctrl+C (KeyboardInterrupt) burada yakalanip `stop_event` set edilir; bu event
    listen_loop() (ears/listener.py) ve speak() (mouth/tts.py) icine geciliyor, ikisi de
    kendi ic dongulerinde bunu periyodik kontrol edip erken cikiyor - kapatma boylece
    disaridan bir exception'in olur olmaz yayilmasina degil, ic bilesenlerin isbirligine
    dayaniyor (graceful shutdown). ONEMLI SINIRLAMA: bu, halihazirda calismakta olan TEK
    bir bloklayici model cagrisini (bir faster-whisper transkripsiyonu, bir Ollama isteği,
    bir XTTS inference chunk'i) yarida kesemez - GPU/senkron cagrilar Python'un sinyal
    kontrol noktalarina donene kadar beklenir; sadece bu cagrilar ARASINDAKI (ve VAD/
    wake-word'un kisa ses-frame'i dongulerindeki) bekleme sürelerini aninda kisaltir.
    """
    # Boot ekrani (ASCII art + gercek Ears/Mouth/Brain yukleme spinner'lari) artik
    # main.py'de, bu fonksiyon cagrilmadan ONCE calisiyor (bkz. main.py) - o noktada
    # modeller zaten gercekten hazir, bu yuzden burada SADECE "dinlemeye basliyorum"
    # bildirimi kaliyor (eskiden buradaki "ONLINE" banner'i hicbir sey yuklenmeden
    # basiliyordu, bkz. docs/TODO.md/plan notlari - yaniltici oldugu icin kaldirildi).
    print_system("Jarvis dinlemeye hazir.", level="success")

    stop_event = threading.Event()
    history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    try:
        # Step 1: Listen (Ears - VAD-bounded utterances via ears.listener.listen_loop)
        for user_text in listen_loop(stop_event=stop_event):
            print_agent("User", user_text)

            # Step 2 + 3: guardrail + dispatcher + Brain (streaming) -> Mouth, cumle cumle.
            # Spinner ilk cumle uretilene kadar acik kalir - sonraki cumleler icin
            # tekrar acilmiyor (Brain zaten stream halinde urettigi icin aralarda
            # gozle gorulur bir bekleme olmuyor, spinner'i her cumlede ac/kapa
            # gereksiz titreme yaratirdi).
            with status_spinner("Jarvis düşünüyor...") as spinner:
                first_sentence = True
                for sentence, lang in _handle_turn(user_text, history, stop_event=stop_event):
                    if first_sentence:
                        spinner.stop()
                        first_sentence = False
                    print_agent("Jarvis", sentence)
                    speak(sentence, language=lang, stop_event=stop_event)
    except KeyboardInterrupt:
        print_system("Kapatma istendi (Ctrl+C) - güvenli şekilde kapatılıyor...", level="warning")
        stop_event.set()
    finally:
        print_system("Jarvis kapatıldı.", level="info")
