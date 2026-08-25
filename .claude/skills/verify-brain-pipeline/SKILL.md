---
name: verify-brain-pipeline
description: Jarvis'in Brain katmanini (main.py, Ollama + llama3.1) mikrofon gerektirmeden bir ornek metinle dogrular. Brain/prompt/main.py degistikten sonra veya "brain'i dogrula" dendiginde kullan.
disable-model-invocation: false
---

## On kosul

Ollama servisi calisiyor ve `llama3.1:8b` modeli cekilmis olmali
(`src/jarvis/brain/llm.py` icindeki `MODEL_NAME` ile birebir eslesmeli -
etiketsiz `llama3.1` Ollama'da 404 hatasi verir):

```
ollama list
```

`llama3.1:8b` listede yoksa `ollama pull llama3.1:8b` calistir.

## Brain'i tek basina dogrula (mikrofon olmadan)

`src/jarvis/brain/llm.py` bagimsiz import edilebilir (Ears/Mouth'u tetiklemez);
`think_and_respond_stream` bir generator - tamamlanan cumleleri tek tek
`yield` eder, tam yaniti gormek icin birlestir:

```
python -c "from src.jarvis.brain.llm import think_and_respond_stream, SYSTEM_PROMPT; h=[{'role':'system','content':SYSTEM_PROMPT}]; print(' '.join(think_and_respond_stream('What is the current time zone concept in one sentence?', h)))"
```

Turkce girdiyle de dene (SYSTEM_PROMPT artik girdinin diline gore cevap
veriyor, Ingilizce'ye sabit degil):

```
python -c "from src.jarvis.brain.llm import think_and_respond_stream, SYSTEM_PROMPT; h=[{'role':'system','content':SYSTEM_PROMPT}]; print(' '.join(think_and_respond_stream('Saat kavrami nedir, tek cumleyle anlat.', h)))"
```

## Dogrulama adimlari

1. Komut hatasiz sonlanmali; `except` bloklarina dusup "Ollama servisine
   bağlanamıyorum" / "System error during cognitive processing: ..." gibi
   bir hata metni donmemeli (Ollama kapaliysa bu beklenen bir davranis,
   ayri bir hata yolu testi sayilir).
2. Yanit, girdiyle AYNI dilde olmali (TR girdiye TR, EN girdiye EN) -
   `SYSTEM_PROMPT` artik dili sabitlemiyor. Markdown isaretleyicisi
   (`**`, `*`, `#`) icermemeli (TTS'e okunacagi icin).
3. Yanit 1-2 cumleyi asmamali (SYSTEM_PROMPT'taki kisitlama).
4. Ayni `history` listesiyle ikinci bir `think_and_respond_stream` cagrisi
   yapilip bir onceki turu hatirladigi (konusma gecmisi) dogrulanabilir.
5. Tam pipeline icin `python main.py` calistirip mikrofona konusarak
   Ears -> Brain -> Mouth zincirini uctan uca dogrula.

Sonucu ozetle: gecti/kaldi, hangi kural ihlal edildi (dil/uzunluk/markdown/
hafiza), varsa SYSTEM_PROMPT veya hata yonetimi icin duzeltme onerisi.
