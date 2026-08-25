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
from src.jarvis.ears.listener import listen_loop
from src.jarvis.mouth.tts import speak

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


def _handle_turn(
    user_text: str, history: list[dict], stop_event: Optional[threading.Event] = None
) -> Iterator[tuple[str, Optional[str]]]:
    """Bir kullanici turunu guardrail + dispatcher'dan gecirip (metin, dil) ciftleri uretir.

    Sira: (1) girdi guardrail'i - reddedilirse Brain'e hic gidilmez, history kirlenmez,
    TEK dilde (tespit edilen girdi diline gore) bir ret mesaji doner; (2) SADECE
    kural-tabanli (LLM'e gitmeyen, bkz. Dispatcher.match_rule) hizli dispatch - bilinen
    ve gercek bir handler'i olan bir intent'se dogrudan onun (metin, dil) cifti donuyor,
    Brain'e hic gidilmiyor; (3) aksi halde normal streaming sohbet - her cumle icin dil
    None donuyor (Brain SYSTEM_PROMPT sayesinde zaten girdi diliyle eslesiyor, speak()'in
    kendi auto-detect'i yeterli), ama once cikti guardrail'inden geciyor, reddedilen
    cumleler atlaniyor.

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
    handler = HANDLERS.get(intent.name) if intent else None
    if handler is not None:
        text, lang = handler(intent)
        yield text, lang
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
