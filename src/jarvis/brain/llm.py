import re
import time
from typing import Iterator, Optional

import httpx
import ollama

from src.jarvis.adapters.agent_factory import ROLE_MODEL_MAP, AgentFactory
from src.jarvis.core.trace import record_trace

SYSTEM_PROMPT_PATH = "system_prompt.txt"
with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as _f:
    SYSTEM_PROMPT = _f.read().strip()

# Sohbet yolu artik `Agent` soyutlamasindan geciyor (Faz 6.2) - hangi model
# kullanildigi ROLE_MODEL_MAP["orchestrator"] (su an hermes3:8b). Bu sabit
# yalnizca /status ekrani ve 404 hata mesaji icin tutuluyor; gercek cagri
# AgentFactory.create("orchestrator").respond_stream() uzerinden.
MODEL_NAME = ROLE_MODEL_MAP["orchestrator"]

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
    """Yerel orkestrator modeline (bkz. ROLE_MODEL_MAP) history-bagli, streaming bir
    istek gonderir; tamamlanan her cumleyi uretildikce yield eder (core.app.run_jarvis()
    bunlari cumle cumle speak()'e besler, boylece TTS tum yanit bitmeden baslar).

    Faz 6.2: `ollama.chat` dogrudan cagrilmiyor, `AgentFactory.create("orchestrator").
    respond_stream()` uzerinden geciliyor - ama cumle bolme, history yonetimi ve
    hata siniflandirmasi (asagidaki 4 `except`) BILINCLI OLARAK burada kaliyor
    (adapter'in respond_stream'i ham hatayi propagate ediyor).

    Basarili turlarda: user + assistant mesajlari history'ye eklenir. Hata
    turlarinda: yalnizca user eklenir (bozuk/bos bir "assistant" mesaji bir
    sonraki turda LLM'e baglam olarak gitmesin), yanit yerine hata metni yield
    edilir.
    """
    _trim_history(history)
    agent = AgentFactory.create("orchestrator")
    parts: list[str] = []
    # Faz 6.9 tracing: bu rol icin kaydedilen `duration_ms` = TIME-TO-FIRST-TOKEN
    # (fonksiyon girisinden ilk chunk'a) - toplam sure DEGIL. Streaming tuketimi
    # `run_jarvis`'te her cumle icin `speak()` ile araya girdiginden toplam
    # wall-clock LLM gecikmesini yansitmaz; TTFT kullanicinin algiladigi "Jarvis
    # konusmaya baslamadan onceki bekleme"dir ve tek anlamli tekil metrik.
    _start = time.perf_counter()
    _ttft_ms: Optional[int] = None
    _trace_result = "success"

    try:
        buffer = ""
        # context=history: system + onceki turlar (bu turun user mesaji HENUZ
        # eklenmedi - respond_stream onu `prompt`tan ekliyor).
        for chunk in agent.respond_stream(user_input, context=history):
            if _ttft_ms is None:
                _ttft_ms = round((time.perf_counter() - _start) * 1000)
            buffer += chunk
            while (match := _SENTENCE_END_RE.search(buffer)):
                sentence = buffer[: match.end()].strip()
                buffer = buffer[match.end() :]
                if sentence:
                    parts.append(sentence)
                    yield sentence
        if buffer.strip():
            parts.append(buffer.strip())
            yield buffer.strip()

        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": " ".join(parts)})
        _trim_history(history)

    except (httpx.ConnectError, ConnectionError):
        # ollama paketi bu hatayi sadece non-streaming yolda ConnectionError'a
        # ceviriyor (bkz. ollama/_client.py _request_raw) - streaming yolda ham
        # httpx.ConnectError siziyor, ikisi de burada yakalaniyor.
        _trace_result = "error"
        history.append({"role": "user", "content": user_input})
        yield (
            "Ollama servisine bağlanamıyorum, çalıştığından emin olun (ollama serve). "
            "I can't reach Ollama - make sure it's running (ollama serve)."
        )
    except ollama.ResponseError as exc:
        _trace_result = "error"
        history.append({"role": "user", "content": user_input})
        if exc.status_code == 404:
            yield (
                f"'{MODEL_NAME}' modeli bulunamadı, 'ollama pull {MODEL_NAME}' ile indirin. "
                f"Model '{MODEL_NAME}' not found - pull it with 'ollama pull {MODEL_NAME}'."
            )
        else:
            yield f"Ollama hatası / Ollama error: {exc}"
    except httpx.TimeoutException:
        # adapters/agent_factory.py:_CLIENT read-timeout'u: Ollama ilk token'i
        # zamaninda dondurmedi (genelde VRAM baskisi altinda model yuklerken
        # takilma). Eskiden timeout yoktu -> ana dongu Ctrl+C'ye kadar donuyordu
        # (canli testte gorulen asil bug).
        _trace_result = "error"
        history.append({"role": "user", "content": user_input})
        yield (
            "Beyin katmanı zamanında yanıt vermedi (muhtemelen VRAM yetersiz), tekrar deneyin. "
            "The brain layer didn't respond in time (likely low on VRAM), please try again."
        )
    except Exception as exc:
        _trace_result = "error"
        history.append({"role": "user", "content": user_input})
        yield f"System error during cognitive processing: {exc}"
    finally:
        # Faz 6.9: fail-soft - record_trace her istisnayi kendi icinde yutar.
        # Generator erken kapatilirsa (GeneratorExit, `_handle_turn` stop_event
        # ile break) de calisir; o durumda _ttft_ms None kalabilir (henuz token
        # gelmedi) - kabul.
        record_trace(
            "orchestrator",
            model=MODEL_NAME,
            input_summary=user_input,
            duration_ms=_ttft_ms,
            result=_trace_result,
        )
