from src.jarvis.brain.llm import SYSTEM_PROMPT, think_and_respond_stream
from src.jarvis.ears.listener import listen_loop
from src.jarvis.mouth.tts import speak


def run_jarvis() -> None:
    """The main execution loop for the MVP pipeline (Ears -> Brain -> Mouth), running continuously.

    Note: dispatcher/guardrail (core.dispatcher, core.guardrail) are not wired in here yet -
    they exist as an independently testable Faz 2 skeleton (see docs/ROADMAP.md Faz 2.1-2.3).
    Routing/guardrail decisions in the live loop are a deliberate next step, not this one.
    """
    print("=== PROJECT JARVIS MVP ONLINE ===")

    history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Step 1: Listen (Ears - VAD-bounded utterances via ears.listener.listen_loop)
    for user_text in listen_loop():
        print(f"\n[USER]: {user_text}")
        print("\n[JARVIS IS THINKING...]")

        # Step 2 + 3: Think (Brain, streaming) -> Respond (text + TTS) cumle cumle
        for sentence in think_and_respond_stream(user_text, history):
            print(f"\n[JARVIS]: {sentence}")
            speak(sentence)
