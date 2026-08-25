import logging
from typing import Iterator

from src.jarvis.brain.llm import SYSTEM_PROMPT, think_and_respond_stream
from src.jarvis.core.dispatcher import Dispatcher
from src.jarvis.core.guardrail.base import GuardrailChain
from src.jarvis.core.guardrail.input_checks import InputInjectionCheck
from src.jarvis.core.guardrail.output_checks import OutputSafetyCheck
from src.jarvis.core.handlers import HANDLERS
from src.jarvis.ears.listener import listen_loop
from src.jarvis.mouth.tts import speak

logger = logging.getLogger("jarvis.core.app")

_INPUT_GUARDRAIL = GuardrailChain([InputInjectionCheck()])
_OUTPUT_GUARDRAIL = GuardrailChain([OutputSafetyCheck()])
_DISPATCHER = Dispatcher()

# Girdi guardrail'i reddettiginde soylenecek sabit, iki dilli mesaj - brain/llm.py'nin
# baglanti/model hata mesajlariyla ayni desen (Brain hic cagrilmadigi icin LLM'e
# kullanicinin dilini "sordurup" cevap uretemeyiz).
_INPUT_REJECTED_MESSAGE = (
    "Bu isteği işleyemiyorum. I can't process that request."
)


def _handle_turn(user_text: str, history: list[dict]) -> Iterator[str]:
    """Bir kullanici turunu guardrail + dispatcher'dan gecirip cumle cumle yanit uretir.

    Sira: (1) girdi guardrail'i - reddedilirse Brain'e hic gidilmez, history kirlenmez;
    (2) SADECE kural-tabanli (LLM'e gitmeyen, bkz. Dispatcher.match_rule) hizli dispatch -
    bilinen ve gercek bir handler'i olan bir intent'se dogrudan onun yaniti donuyor,
    Brain'e hic gidilmiyor; (3) aksi halde normal streaming sohbet - ama her cumle,
    speak()'e gitmeden once cikti guardrail'inden geciyor, reddedilen cumleler atlaniyor.
    """
    input_result = _INPUT_GUARDRAIL.run(user_text)
    if not input_result.allowed:
        yield _INPUT_REJECTED_MESSAGE
        return

    intent = _DISPATCHER.match_rule(user_text)
    handler = HANDLERS.get(intent.name) if intent else None
    if handler is not None:
        yield handler(intent)
        return

    for sentence in think_and_respond_stream(user_text, history):
        output_result = _OUTPUT_GUARDRAIL.run(sentence)
        if output_result.allowed:
            yield sentence
        # Reddedilen cumle sessizce atlanir - GuardrailChain zaten nedenini logluyor;
        # bir sohbetin ortasinda garip bir "engellendi" anonsu okumak yerine akici kalir.


def run_jarvis() -> None:
    """The main execution loop for the MVP pipeline (Ears -> guardrail/dispatcher -> Brain -> Mouth)."""
    print("=== PROJECT JARVIS MVP ONLINE ===")

    history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Step 1: Listen (Ears - VAD-bounded utterances via ears.listener.listen_loop)
    for user_text in listen_loop():
        print(f"\n[USER]: {user_text}")
        print("\n[JARVIS IS THINKING...]")

        # Step 2 + 3: guardrail + dispatcher + Brain (streaming) -> Mouth, cumle cumle
        for sentence in _handle_turn(user_text, history):
            print(f"\n[JARVIS]: {sentence}")
            speak(sentence)
