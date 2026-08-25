import re
from typing import Iterator

import httpx
import ollama

SYSTEM_PROMPT_PATH = "system_prompt.txt"
with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as _f:
    SYSTEM_PROMPT = _f.read().strip()

# We are using Llama 3.1 (8B) as our local brain
MODEL_NAME = "llama3.1:8b"

# Son N mesaj (system haric) tutulur - yerel 8B modelde her ek mesaj hem
# gecikmeyi hem islem yukunu buyutur, MVP icin son birkac turluk baglam
# yeterli. 12 = son 6 kullanici + 6 asistan turu.
MAX_HISTORY_MESSAGES = 12

# Akan (streaming) yanitin cumlelere bolunmesi icin: noktalama + bosluk
# sonrasi bolunur, boylece "3.5" veya hala uretilmekte olan bir cumlenin
# ortasindaki nokta erken bolunmeye yol acmaz (bosluk henuz gelmediyse).
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


def _trim_history(history: list[dict]) -> list[dict]:
    """History'yi MAX_HISTORY_MESSAGES'e kirpar, system mesaji (index 0) her zaman kalir."""
    if len(history) - 1 > MAX_HISTORY_MESSAGES:
        overflow = (len(history) - 1) - MAX_HISTORY_MESSAGES
        del history[1 : 1 + overflow]
    return history


def think_and_respond_stream(user_input: str, history: list[dict]) -> Iterator[str]:
    """Local Llama 3.1'e history-bagli, streaming bir istek gonderir; tamamlanan her
    cumleyi uretildikce yield eder (core.app.run_jarvis() bunlari cumle cumle speak()'e
    besler, boylece TTS tum yanit bitmeden baslar). Basarili turlarda tam yanit history'ye
    assistant mesaji olarak eklenir; hata turlarinda history'ye hicbir sey eklenmez
    (bozuk/bos bir "assistant" mesaji bir sonraki turda LLM'e baglam olarak gitmesin).
    """
    history.append({"role": "user", "content": user_input})

    try:
        stream = ollama.chat(model=MODEL_NAME, messages=_trim_history(history), stream=True)

        buffer = ""
        parts: list[str] = []
        for chunk in stream:
            buffer += chunk["message"]["content"]
            while (match := _SENTENCE_END_RE.search(buffer)):
                sentence = buffer[: match.end()].strip()
                buffer = buffer[match.end() :]
                if sentence:
                    parts.append(sentence)
                    yield sentence
        if buffer.strip():
            parts.append(buffer.strip())
            yield buffer.strip()

        history.append({"role": "assistant", "content": " ".join(parts)})

    except (httpx.ConnectError, ConnectionError):
        # ollama paketi bu hatayi sadece non-streaming yolda ConnectionError'a
        # ceviriyor (bkz. ollama/_client.py _request_raw) - streaming yolda ham
        # httpx.ConnectError siziyor, ikisi de burada yakalaniyor.
        yield (
            "Ollama servisine bağlanamıyorum, çalıştığından emin olun (ollama serve). "
            "I can't reach Ollama - make sure it's running (ollama serve)."
        )
    except ollama.ResponseError as exc:
        if exc.status_code == 404:
            yield (
                f"'{MODEL_NAME}' modeli bulunamadı, 'ollama pull {MODEL_NAME}' ile indirin. "
                f"Model '{MODEL_NAME}' not found - pull it with 'ollama pull {MODEL_NAME}'."
            )
        else:
            yield f"Ollama hatası / Ollama error: {exc}"
    except Exception as exc:
        yield f"System error during cognitive processing: {exc}"
