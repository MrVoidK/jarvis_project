---
name: verify-brain-pipeline
description: Jarvis'in Brain katmanini (main.py, Ollama + llama3.1) mikrofon gerektirmeden bir ornek metinle dogrular. Brain/prompt/main.py degistikten sonra veya "brain'i dogrula" dendiginde kullan.
disable-model-invocation: false
---

## On kosul

Ollama servisi calisiyor ve `llama3.1:8b` modeli cekilmis olmali (`main.py`
icindeki `MODEL_NAME` ile birebir eslesmeli - etiketsiz `llama3.1` Ollama'da
404 hatasi verir):

```
ollama list
```

`llama3.1:8b` listede yoksa `ollama pull llama3.1:8b` calistir.

## Brain'i tek basina dogrula (mikrofon olmadan)

`main.py`'yi import etmek `audio_handler`'i da import edip Whisper modelini
yukler (yavas); mikrofonsuz test etmek icin dogrudan `think_and_respond`'u
cagir:

```
python -c "from main import think_and_respond; print(think_and_respond('What is the current time zone concept in one sentence?'))"
```

## Dogrulama adimlari

1. Komut hatasiz sonlanmali; `think_and_respond` icindeki `except` bloguna
   dusup "System error during cognitive processing: ..." donmemeli.
2. Yanit Ingilizce olmali (SYSTEM_PROMPT kurali), markdown isaretleyicisi
   (`**`, `*`, `#`) icermemeli (TTS'e okunacagi icin).
3. Yanit 1-2 cumleyi asmamali (SYSTEM_PROMPT'taki kisitlama).
4. Tam pipeline icin `python main.py` calistirip mikrofona konusarak
   Ears -> Brain zincirini uctan uca dogrula.

Sonucu ozetle: gecti/kaldi, hangi kural ihlal edildi (Ingilizce/uzunluk/
markdown), varsa SYSTEM_PROMPT veya hata yonetimi icin duzeltme onerisi.
