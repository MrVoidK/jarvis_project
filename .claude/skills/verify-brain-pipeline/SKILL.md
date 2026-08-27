---
name: verify-brain-pipeline
description: Jarvis'in Brain katmanini (main.py, Ollama + hermes3:8b) mikrofon gerektirmeden bir ornek metinle dogrular. Brain/prompt/main.py degistikten sonra veya "brain'i dogrula" dendiginde kullan.
disable-model-invocation: false
---

## On kosul

Ollama servisi calisiyor ve `hermes3:8b` modeli cekilmis olmali (Faz 6.2:
orchestrator/sohbet modeli artik `adapters/agent_factory.py:ROLE_MODEL_MAP
["orchestrator"]` = `hermes3:8b`; `brain/llm.py:MODEL_NAME` bunu yansitir -
etiketsiz `hermes3` Ollama'da 404 verir):

```
ollama list
```

`hermes3:8b` listede yoksa `ollama pull hermes3:8b` calistir. (Router yolu
ayrica `qwen2.5:3b` kullanir - bu skill sadece sohbet yolunu dogrular,
router icin `verify-multiagent-integration` skill'ine bak.)

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
