import logging
import threading
from typing import Iterator, Optional

from src.jarvis.brain.llm import SYSTEM_PROMPT, think_and_respond_stream
from src.jarvis.core.dispatcher import Dispatcher
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
    """
    lang = intent.parameters.get("lang", "en")
    content = intent.parameters.get("content")

    # (1) Icerik tasiyan araclarda (ozellikle run_command) metni, LLM ciktisi icin
    # kullandigimiz ayni guardrail'den geciriyoruz - kullaniciya onay bile sorulmadan
    # bilinen yikici kaliplar (rm -rf, format, DROP TABLE...) reddedilsin diye
    # (defense-in-depth: yanlislikla "Y"ye basma ihtimali bu kaliplar icin dogmuyor).
    if content:
        safety = _OUTPUT_GUARDRAIL.run(content)
        if not safety.allowed:
            logger.warning("Tool girdisi guardrail'e takildi (%s): %s", tool.name, safety.reason)
            return _localized(_UNSAFE_COMMAND_MESSAGES, lang)

    # (2) Orta ve uzeri risk -> zorunlu insan onayi. Kullanici ekrana bakmiyor
    # olabilecegi icin once sesli uyariyoruz, sonra terminalde bloklayici soru.
    if requires_approval(tool.risk_level):
        speak(_localized(_APPROVAL_PENDING_MESSAGES, lang), language=lang, stop_event=stop_event)
        prompt = f"'{tool.name}' calistirilsin mi? (risk: {tool.risk_level.value})"
        if content:
            prompt += f"\n  -> {content}"  # kullanici TAM METNI gorsun (bkz. tools/shell.py)
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
    TEK dilde (tespit edilen girdi diline gore) bir ret mesaji doner; (2) SADECE
    kural-tabanli (LLM'e gitmeyen, bkz. Dispatcher.match_rule) hizli dispatch - once
    risk-tasimayan HANDLERS (orn. get_time), sonra risk-kontrollu TOOL_REGISTRY (Faz 3
    araclari, bkz. _execute_tool); ikisinde de Brain'e hic gidilmiyor; (3) aksi halde
    normal streaming sohbet - her cumle icin dil None donuyor (Brain SYSTEM_PROMPT
    sayesinde zaten girdi diliyle eslesiyor, speak()'in kendi auto-detect'i yeterli),
    ama once cikti guardrail'inden geciyor, reddedilen cumleler atlaniyor.

    `stop_event` verilirse, Brain'in streaming yanitini urettigi surece her cumle
    sonrasi kontrol edilir - kapatma istenirse kalan cumleler beklenmeden erken cikilir.
    """
    input_result = _INPUT_GUARDRAIL.run(user_text)
    if not input_result.allowed:
        lang = detect_language(user_text)
        message = _INPUT_REJECTED_MESSAGES.get(lang, _INPUT_REJECTED_MESSAGES["en"])
        yield message, lang if lang in _INPUT_REJECTED_MESSAGES else "en"
        return

    intent = _DISPATCHER.match_rule(user_text)
    if intent is not None:
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
    print("=== PROJECT JARVIS MVP ONLINE ===")
    logger.info("Tum modeller yuklendi, Jarvis dinlemeye hazir.")

    stop_event = threading.Event()
    history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    try:
        # Step 1: Listen (Ears - VAD-bounded utterances via ears.listener.listen_loop)
        for user_text in listen_loop(stop_event=stop_event):
            print(f"\n[USER]: {user_text}")
            print("\n[JARVIS IS THINKING...]")

            # Step 2 + 3: guardrail + dispatcher + Brain (streaming) -> Mouth, cumle cumle
            for sentence, lang in _handle_turn(user_text, history, stop_event=stop_event):
                print(f"\n[JARVIS]: {sentence}")
                speak(sentence, language=lang, stop_event=stop_event)
    except KeyboardInterrupt:
        logger.info("Kapatma istendi (Ctrl+C) - guvenli sekilde kapatiliyor...")
        stop_event.set()
    finally:
        logger.info("Jarvis kapatildi.")
