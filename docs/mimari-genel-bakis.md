# JARVIS — Sistem Mimarisi Genel Bakış (Geliştirme Planlaması İçin)

> Bu doküman, projenin **şu anki (çalışan) durumunu** ayrı bir sohbette
> mimari geliştirme planlaması yapabilmek için tek başına yeterli olacak
> şekilde özetler. Hedef/gelecek mimari için `docs/ARCHITECTURE.md`, faz
> planı için `docs/ROADMAP.md`, kısa özet için kökteki `CLAUDE.md`.
>
> Kapsam: genel mimari · LLM ve ajan çalışma mantığı · aksiyon (tool)
> çalışma mantığı · ana AI döngüsü · dosya yapısı · önemli fonksiyonlar.
>
> Platform: Windows 11 + RTX 4070 (12 GB VRAM) · Python (venv) · tamamen
> senkron/thread-tabanlı çekirdek (asyncio yalnızca HUD köprüsünde).

---

## 1. Bir Bakışta Sistem

JARVIS; **yerel-öncelikli**, terminal tabanlı, sesli + yazılı bir kişisel
asistan. Beş katmanlı bir hattır:

```
   ┌─────────┐   ses/metin    ┌──────────────┐   intent    ┌──────────────┐
   │  EARS   │ ─────────────▶ │  GUARDRAIL   │ ──────────▶ │  DISPATCHER  │
   │ + metin │                │  (girdi)     │             │ (router)     │
   └─────────┘                └──────────────┘             └──────┬───────┘
   wake-word / VAD                                                │
   faster-whisper                              ┌───────────────────┼───────────────────┐
                                               ▼                   ▼                   ▼
                                       HANDLERS (get_time)   TOOL_REGISTRY        BRAIN (chat)
                                       (LLM'siz cevap)       + risk/onay          Ollama llama3.1
                                                             + guardrail          streaming + hafıza
                                               │                   │                   │
                                               └───────────────────┼───────────────────┘
                                                                   ▼
                                                          GUARDRAIL (çıktı)
                                                                   ▼
                                                          ┌──────────────┐
                                                          │  MOUTH (TTS) │  XTTS-v2
                                                          │  + JARVIS HUD│  FastAPI/WS + web-ui
                                                          └──────────────┘
```

**Çekirdek fikirler:**

| İlke | Kodda karşılığı |
|---|---|
| Modüler ajan mimarisi (her yetenek ayrı paket) | `src/jarvis/{ears,brain,mouth,core,adapters,agents,tools}/` |
| Sağlayıcı bağımsızlığı (Factory + Adapter) | `agents/base.py:Agent`, `adapters/agent_factory.py` |
| Human-in-the-loop / Zero-Trust | `core/risk.py` ([Y/N] onayı), `core/guardrail/` |
| Tek merkezi güvenlik kararı (tool'a bırakılmaz) | `core/app.py:_run_tool_pipeline()` |
| Statik tool kaydı (otomatik keşif yok) | `tools/registry.py:TOOL_REGISTRY` |
| Hibrit girdi (mikrofon + terminal eşzamanlı) | `core/input_hub.py:InputHub` |
| "Sıra dışı her mimari kararın yanına yorum" | Modül docstring'leri yoğun ve gerekçeli |

---

## 2. Süreç Başlatma (`main.py`)

`python main.py` → `main.py` **import zamanında** (fonksiyon çağrısı
beklemeden) ağır alt sistemleri sırayla, görünür spinner'larla yükler:

1. `core/console.py:setup_logging()` — tüm `logging` çıktısını tek bir
   `RichHandler`'a bağlar (en erken, çünkü sonraki import'lar log basar).
2. `print_boot_sequence()` — ASCII "J.A.R.V.I.S" logosu.
3. `import src.jarvis.ears.listener` → **modül üstünde** faster-whisper
   (`turbo`) + openWakeWord modellerini yükler (Singleton deseni).
4. `import src.jarvis.mouth.tts` → **modül üstünde** XTTS-v2 yükler +
   referans ses embedding'lerini hesaplar.
5. `check_ollama_connection()` → `ollama.show("llama3.1:8b")` ile hafif
   doğrulama (sunucu ayakta mı + model çekilmiş mi).
6. `core/api.py:start_api_server_thread()` → uvicorn/FastAPI **ayrı daemon
   thread**'te (HUD WebSocket köprüsü, port 8000).
7. `core/web_ui_process.py:start_web_ui_dev_server()` → `web-ui/`de
   `npm run dev` (Vite, port 5173) **alt-süreç** olarak; `atexit` ile
   kapanışta `taskkill /T /F`. Ön koşul yoksa sessizce atlanır.
8. `run_jarvis()` → ana döngü (aşağıda §3).

> **Neden import-zamanı yükleme:** Ears/Mouth modülleri `sys.modules`
> cache'i sayesinde `core.app` onları tekrar import ettiğinde bedelsiz
> gelir; boot ekranı gerçek yükleme sırasını yansıtır.

---

## 3. Ana AI Döngüsü — `core/app.py:run_jarvis()`

Döngünün tamamı **tek bir ana thread**'te, sırayla çalışır. Girdi
toplama iki arka plan thread'ine (mikrofon + stdin) delege edilmiştir;
ana thread yalnızca birleşik kuyruktan okur.

### 3.1 Kurulum

```python
stop_event      = threading.Event()   # graceful shutdown (Ctrl+C / "sistemi kapat" / /exit)
speaking_event  = threading.Event()   # Jarvis konuşurken set — mikrofon kendini duymasın (AEC yok)
history         = [{"role": "system", "content": SYSTEM_PROMPT}]

hub = InputHub(stop_event, speaking_event); hub.start()   # mic thread + text thread
api.register_input_hub(hub)                                # HUD'dan gelen yazılı komutlar için
Scheduler(...).start()          # jarvis-scheduler daemon (opt-in, cron -> InputEvent source="scheduled")
ContinuousRunner(...).start()   # jarvis-continuous daemon (opt-in, koşul izleme -> source="continuous")
pending: list[InputEvent] = []                             # onay beklerken biriken "voice" olayları
```

### 3.2 Döngü adımları (`while not stop_event.is_set()`)

1. **Olay al:** `event = pending.pop(0) if pending else hub.next_event()`
   — `hub.next_event()` birleşik `queue.Queue`'dan 0.5 sn poll'lü
   `get()` yapar (Ctrl+C'nin anında işlenebilmesi için süresiz `get()`
   bilinçli olarak kullanılmaz).
2. **CLI komutu mu?** `event.source == "text"` ve `is_cli_command(text)`
   (`/` ile başlıyor) → `core/cli_commands.py:handle_cli_command()`;
   **Guardrail/Dispatcher/Brain/TTS'e hiç uğramaz**. `continue`.
3. `print_agent(...)` ile diyaloğu bas; `hud_bus.publish_state("processing")`.
4. `with status_spinner("Jarvis düşünüyor..."):` içinde
   `_handle_turn(event.text, history, ...)` generator'ını tüket:
   - her `(sentence, lang)` çiftinde: ilk cümlede spinner'ı durdur
     (`_stop_spinner_once`, idempotent), `print_agent("Jarvis", sentence)`,
     `speak(sentence, language=lang, stop_event, speaking_event)`.
5. `KeyboardInterrupt` → `stop_event.set()`; `finally` → "Jarvis kapatıldı".

### 3.3 `_handle_turn()` — bir kullanıcı turunun karar ağacı

```
user_text
  │
  ├─(1) _INPUT_GUARDRAIL.run(user_text)   # GuardrailChain([InputInjectionCheck()])
  │        └─ RED → detect_language() ile TEK dilde ret mesajı yield; DUR
  │
  ├─(2) intent = _DISPATCHER.classify(user_text)      # §4
  │
  ├─(3) intent.name != "chat" ise:
  │        ├─ source=="llm" → print_router_decision(...)   # sadece gerçek araç seçildiyse panel
  │        ├─ name == "shutdown" → stop_event.set(); veda mesajı yield; DUR
  │        ├─ HANDLERS.get(name) varsa → handler(intent) → (text, lang) yield; DUR   # get_time
  │        └─ get_tool(name) varsa → _execute_tool(...) → (result, lang) yield; DUR   # §5
  │
  └─(4) aksi halde (chat): think_and_respond_stream(user_text, history)   # §6 — Brain
           her cümle için:
             ├─ stop_event.is_set() → kalan cümleleri atla
             ├─ _OUTPUT_GUARDRAIL.run(sentence)   # GuardrailChain([OutputSafetyCheck()])
             │     └─ RED → cümleyi sessizce atla (akış bozulmasın)
             └─ (sentence, None) yield   # lang=None: SYSTEM_PROMPT dili zaten eşliyor, speak() auto-detect eder
```

### 3.4 Graceful shutdown — üç tetik, tek mekanizma

`stop_event` set edilir; döngü **bir sonraki iterasyonda** kendiliğinden
çıkar. Exception fırlatılmaz.

| Tetik | Yol |
|---|---|
| Ctrl+C | `run_jarvis()` `KeyboardInterrupt` yakalar → `stop_event.set()` |
| Sesli/yazılı "sistemi kapat" / "shut down" | `_RULES` fast-path → `SHUTDOWN_INTENT_NAME` → `_handle_turn` `stop_event.set()` |
| `/exit` (terminal veya HUD) | `cli_commands.py:_cmd_exit()` → `stop_event.set()` |

`stop_event` ayrıca `InputHub` thread'lerine, `speak()`'e, `listen_loop()`'a
geçirilir; hepsi kendi iç döngülerinde periyodik kontrol edip erken çıkar.
**Sınırlama:** halihazırda çalışan **tek bir bloklayıcı çağrı** (bir
Whisper transkripsiyonu, bir Ollama isteği, bir XTTS chunk'ı, bir `input()`)
yarıda kesilemez — yalnızca çağrılar arası bekleme kısalır.

---

## 4. Girdi Katmanı

### 4.1 Ears — `ears/listener.py`

`listen_loop()` bir **state machine**, tek kalıcı `sd.InputStream` üzerinde:

| Durum | Ne yapar |
|---|---|
| **IDLE** | openWakeWord `hey_jarvis` (ONNX) + çift-alkış (RMS/crest-factor, dinamik gürültü tabanı EMA) beklenir. Hiçbir şey transkribe edilmez. |
| **ACTIVE** | Wake-word/alkış sonrası `webrtcvad` ile VAD-endpointing'li dinamik kayıt (diske yazmadan `ndarray`). Pre-roll (~90 ms) ile ilk hece kırpılmaz. |
| **FOLLOWUP** | Bir utterance bittikten sonra wake-word gerekmeden ~12 sn takip penceresi. Pencere **yalnızca gerçek metin üreten** bir turdan sonra sıfırlanır; gürültü kaynaklı boş turlar kalan süreyi tüketir (IDLE'a dönüşü süresiz ertelemez). |

Transkripsiyon: `faster-whisper` `turbo` (= large-v3-turbo),
`multilingual=True` + TR/EN karışık `initial_prompt`, `vad_filter=True`.
**CUDA/float16 → CPU/int8 otomatik fallback** (`_load_model_with_fallback()`
CUDA'da sessiz bir warm-up transkripsiyonuyla gerçek hatayı erken tetikler).
Windows'ta `cublas64_12.dll` eksikliği modül başındaki DLL-fix
(`os.add_dll_directory` + `nvidia-cublas-cu12`/`nvidia-cudnn-cu12` pip
paketleri) ile çözülür.

Opsiyonel kancalar (modül üst katmanı bilmez, sadece callback çağırır):
- `stop_event` — graceful shutdown.
- `mute_event` — Jarvis konuşurken (`speaking_event`) yeni tetikleme aranmaz.
- `on_state_change` — HUD için `"idle"`/`"listening"` yayını (`hud_bus.publish_state`).

### 4.2 Hibrit girdi — `core/input_hub.py:InputHub`

Mikrofon ve terminal metnini **eşzamanlı** dinler, **tek sıralı
`queue.Queue`**'da birleştirir. Ana thread yalnızca kuyruktan okur.

- `_mic_producer` — `listen_loop()`'u arka plan thread'inde tüketir,
  transkriptleri `InputEvent(source="voice", ...)` olarak kuyruğa koyar.
  (`ears.listener` import'u fonksiyon içinde/gecikmeli — testlerin ağır
  model yüklemesini tetiklememesi için.)
- `_text_producer` — **stdin'in TEK sahibi**; `console.input()` döngüsü,
  `InputEvent(source="text", ...)`. `EOFError` (kapalı stdin) → thread
  sessizce biter.
- `submit_external_text(text)` — HUD WebSocket thread'inden gelen yazılı
  komutu aynı kuyruğa koyar (`queue.Queue` zaten thread-safe).
- `core/scheduler.py:Scheduler` — `jarvis-scheduler` daemon; cron ifadesi denk
  gelince `hub.submit_event(InputEvent("scheduled", <onceden tanimli metin>))`.
  `config/scheduled_tasks.yaml` yoksa hic baslamaz.
- `core/continuous_runner.py:ContinuousRunner` — `jarvis-continuous` daemon; bir
  koşulu (bu fazda dosya mtime) izler, tetiklenince
  `hub.submit_event(InputEvent("continuous", ...))`.

**STDIN sahipliği (kritik):** Onay beklerken ana thread kendi `input()`'unu
**çağırmaz** (metin thread'iyle stdin yarışı olurdu). Bunun yerine
`wait_for_text_answer(pending)` paylaşılan kuyruktan bir sonraki `"text"`
olayını bekler:
- Çağrı anında kuyrukta zaten bekleyen her şey önce `pending`'e boşaltılır
  → **sadece bundan sonra** gelen `"text"` gerçek cevap sayılır (alakasız
  bir önceki mesaj "evet" gibi tüketilemez — security-reviewer bulgusu).
- Arada gelen `"voice"` olayları `pending`'e eklenir, onay sonrası normal
  tur olarak işlenir (kullanıcının sözü kaybolmaz).
- Metin thread'i ölmüşse (stdin kapalı) → boş string → `False` (RET);
  sonsuz askıda kalma yok.

---

## 5. Brain — LLM Sohbet Katmanı (`brain/llm.py`)

- Model: **Ollama `llama3.1:8b`** (yerel).
- `think_and_respond_stream(user_input, history)`:
  - `history`'ye `user` mesajı ekler.
  - `ollama.chat(model, messages=_trim_history(history), stream=True)`.
  - Akan chunk'ları `_SENTENCE_END_RE` (`(?<=[.!?])\s+`) ile **cümlelere
    böler**, tamamlanan her cümleyi `yield` eder → Mouth cümle cümle
    beslenir (TTS tüm yanıt bitmeden başlar).
  - Başarılıysa tam yanıt `history`'ye `assistant` olarak eklenir;
    **hata turlarında history'ye hiçbir şey eklenmez** (bozuk bağlam
    sonraki tura sızmasın).
- Hafıza: `history` döngü boyunca kalıcı; `MAX_HISTORY_MESSAGES = 12`
  (system hariç son 12 mesaj; eskiler `_trim_history()` ile kırpılır,
  index 0 system her zaman kalır).
- `SYSTEM_PROMPT` kökteki `system_prompt.txt`'ten okunur: kısa/öz yanıt,
  markdown yok (TTS okuyacak), **kullanıcının dilinde** (TR/EN) yanıt,
  "araçları sen çalıştıramazsın; tanınmayan istek gelirse sadece
  anlamadığını söyle, bahane uydurma".
- Hata sınıflandırması (TR+EN tek mesaj): `httpx.ConnectError`/
  `ConnectionError` (Ollama kapalı), `ollama.ResponseError` 404 (model yok),
  genel exception.

---

## 6. Ajan Mimarisi (Factory + Adapter)

### 6.1 `agents/base.py:Agent` (ABC) — ortak sözleşme

```python
class Agent(ABC):
    def respond(self, prompt, context=None) -> str: ...
    def supports_tools(self) -> bool: ...
    def call_tools(self, prompt, tools: list[dict], context=None) -> AgentToolResponse: ...
```

`AgentToolResponse(tool_calls: list[ToolCall], content: str | None)`,
`ToolCall(name: str, arguments: dict)`. `tools` parametresi bilinçli olarak
Ollama/OpenAI-stili function-calling şeması (fiili endüstri standardı —
aşırı soyutlama YAGNI sayıldı).

### 6.2 `adapters/agent_factory.py:AgentFactory`

`AgentFactory.create(role)` — çağıran kod somut sınıfı hiç görmez:

| role | Adapter | Model | Durum |
|---|---|---|---|
| `"orchestrator"` | `LlamaOrchestratorAdapter` | `llama3.1:8b` | **Aktif** — `respond()` + `call_tools()` tam çalışıyor. Semantic router BUNU kullanır. |
| `"tool_agent"` | `HermesAgentAdapter` | `hermes3:8b` | Kısmi — `respond()` var; `call_tools()` **`NotImplementedError`** (router'a bağlı değil). |
| `"deep_reasoning"` | `ClaudeCodeAdapter` | (Anthropic) | **Stub** — hepsi `NotImplementedError`. `anthropic` SDK kurulu değil, key yok. |

`LlamaOrchestratorAdapter.call_tools()`: `ollama.chat(..., tools=tools,
options={"temperature": 0.1})` — düşük sıcaklık bilinçli (küçük model
"her zaman bir araç seç" önyargısını azaltmak için). `raw_calls` beklenmedik
biçimdeyse `KeyError/TypeError/ValueError` yakalanıp **boş liste** (= araç
seçilmedi) döner — süreç çökmez (security-reviewer bulgusu).

`check_ollama_connection(model)` — boot'ta `ollama.show()` ile hafif
doğrulama; adapter'ın `respond()`'uyla aynı hata sınıflandırması.

> **Önemli:** Şu an mimaride tek gerçek "LLM ajanı" Orkestratör'dür ve iki
> ayrı rolde çağrılır: (a) **router** (`dispatcher.py`, `call_tools`),
> (b) **sohbet** (`brain/llm.py`, doğrudan `ollama.chat` streaming — bu
> yol `Agent` arayüzünü **kullanmıyor**, tarihsel olarak Factory'den önce
> yazıldı). Multi-agent (Hermes delegasyonu, Claude Code alt-yüklenici)
> henüz kod düzeyinde bağlı değil.

---

## 7. Semantic Router / Dispatcher (`core/dispatcher.py`)

`Dispatcher.classify(text) -> Intent` — her transkripti bir `Intent`'e
çevirir: `name`, `confidence` (0–1), `parameters: dict`, `source`
(`"rule"` | `"llm"`).

### 7.1 Aşama 1 — Fast-path regex (`match_rule`, LLM'e gitmez)

`_RULES` sadece **belirsizlik taşımayan** komutlar için:
- `get_time` — `\bsaat kaç\b` / `\bwhat time is it\b`
- `SHUTDOWN_INTENT_NAME` (`"shutdown"`) — `\b(sistemi|kendini) kapat\b`,
  `\bshut ?down\b`, `\bturn (yourself|the system) off\b`

Dile göre ayrı pattern → eşleşen alternatifin dili `parameters["lang"]`'a
kesin olarak yazılır (langdetect kısa metinde güvenilmez).

### 7.2 Aşama 2 — Ollama native tool-calling (router)

Kural eşleşmezse:

1. `tools_schema = build_ollama_tools(all_tools().values()) + [_NO_TOOL_SCHEMA]`
   — yerel `TOOL_REGISTRY` + MCP-keşfedilen araçların birleşik görünümü,
   her `Tool` kendi JSON-Schema'sını taşır (`adapters/tool_schema.py`).
2. `orchestrator = AgentFactory.create("orchestrator")`.
3. `context = [{"role": "system", "content": _ROUTER_SYSTEM_PROMPT}]`.
4. `response = orchestrator.call_tools(text, tools=tools_schema, context=context)`.
5. Sonuç yorumu:
   - `tool_calls` boş → `Intent("chat", 0.4, source="llm")`.
   - `call.name == "no_tool_needed"` (sentinel) → `Intent("chat", 0.6)`.
   - `get_tool(call.name) is None` → `Intent("chat", 0.3)`.
   - `validate_arguments(tool, call.arguments) is None` (şema uyumsuz /
     beklenmeyen tip) → `Intent("chat", 0.3)` — **fail-closed**.
   - aksi halde → `Intent(call.name, 0.9, source="llm",
     parameters={**validated, "lang": detect_language(text)})`.

### 7.3 `_NO_TOOL_FUNCTION_NAME = "no_tool_needed"` — neden var

`ollama.chat(..., tools=[...])` çağrıldığında `llama3.1:8b`'nin Ollama
şablonu kullanıcının son turunu sunucu tarafında **koşulsuz** "bir
fonksiyon çağır" çerçevesine sokar; "hiçbir fonksiyon çağırma" dalı yoktur.
Prompt'la ("araç yoksa çağırma") savaşmak ve temperature düşürmek işe
yaramadı (sunucu-tarafı şablon davranışı). Çözüm: modele bu çerçeveyle
**çalışan** somut bir "kaçış" fonksiyonu vermek. `TOOL_REGISTRY`'ye
eklenmez — sadece router şemasına eklenir.

### 7.4 `_ROUTER_SYSTEM_PROMPT` — davranış kuralları

- "Bir fonksiyon açıkça uyuyorsa SADECE onu çağır; sohbet/selam/genel soru
  ise `no_tool_needed`."
- Emin değilsen `no_tool_needed` ("kaçırılmış tool call, yanlış olandan
  ucuzdur").
- Belirli bir şarkı/sanatçı istenirse **MUTLAKA `search_music`** — asla
  `run_command` ile URL/dosya yolu uydurma.
- `run_command` için asla yol/URL/komut uydurma; sadece kullanıcının
  **birebir dikte ettiği** komut.
- "Terminal/komut" kelimesini yalnızca **içeren** ama komut dikte
  **etmeyen** cümle (ör. Jarvis'in kendi "terminale bakın" anonsu) bir
  komut değildir → `no_tool_needed`.

### 7.5 Bilinen maliyet

Kural eşleşmeyen **her turda** iki LLM çağrısı olabilir: router + (chat'e
düşerse) Brain. Roadmap'te "router+chat'i tek çağrıya birleştir" veya
"daha küçük/hızlı router modeli" iyileştirmesi not düşülmüş.

---

## 8. Aksiyon (Tool) Katmanı

### 8.1 `tools/base.py:Tool` (ABC)

```python
class Tool(ABC):
    name: str
    description: str
    risk_level: RiskLevel
    parameters_schema: dict = {}        # JSON-Schema "properties"
    required_parameters: list[str] = []
    def execute(self, params: dict) -> str: ...   # TTS'e okunacak KISA, TEK dilli cümle döner
```

`params` her zaman `"lang"` içerir (dispatcher ekler). Dönen metin
doğrudan TTS'e gider → kısa, markdown'sız, tek dilli olmalı.
**Güvenlik kararı tool'a bırakılmaz** — risk/onay/guardrail tek merkezde
(`core/app.py`).

### 8.2 `tools/registry.py:TOOL_REGISTRY`

Statik `dict` — her tool **açıkça import edilip** elle konur. Otomatik
keşif yok ("bir araç yanlışlıkla kayıtlı olamaz" = güvenlik özelliği).
Anahtarlar router'a bildirilen araç adlarıyla birebir aynı olmalı.

- `all_tools()` → **üç kaynağın** birleşik görünümü (Faz 6.4): statik
  `TOOL_REGISTRY` + `core/registry_loader.py:load_dynamic_tools()`
  (allowlist'li `agents/registry/*.yaml` manifest'leri) + MCP keşfi.
  `TOOL_REGISTRY`'nin kendisi değişmez; öncelik **statik > dinamik manifest
  > MCP** ve statik ad çakışmasında her zaman kazanır.
- `get_tool(name)` → önce statik, sonra dinamik manifest, en son MCP.
- Dinamik manifest: dosya koymak tek başına aktive etmez — dosya kökü ayrıca
  `config/security.yaml:enabled_dynamic_agents` allowlist'inde olmalı.

### 8.3 Kayıtlı araçlar

| name | Sınıf / dosya | Risk | Parametre | Ne yapar |
|---|---|---|---|---|
| `create_note` | `CreateNoteTool` / `notes_tool.py` | MEDIUM | `content` | Notu zaman damgasıyla Obsidian vault'undaki **sabit** `<vault>/Jarvis Notes/Jarvis Log.md`'ye ekler. Dosya adı asla LLM'den gelmez. `is_path_safe()` ikinci katman. |
| `read_notes` | `ReadNotesTool` / `notes_tool.py` | MEDIUM | — | Son 5 notu SESLİ okur. Salt-okunur ama MEDIUM: FOLLOWUP penceresinde yanlış tetiklenirse kişisel veri ifşası olurdu ("bedel" = bilgi ifşası). |
| `list_files` | `ListFilesTool` / `files.py` | LOW | — | Sadece `PROJECT_ROOT/jarvis_workspace/` içeriğini listeler. Yol parametresi yok → path traversal yüzeyi yok. |
| `run_command` | `RunCommandTool` / `terminal_tool.py` | **HIGH** | `command` | Kullanıcının birebir dikte ettiği Windows komutunu (onay sonrası) `subprocess.Popen(shell=True)` + `communicate(timeout=15)` ile çalıştırır. Timeout'ta `taskkill /F /T` ile tüm süreç ağacı öldürülür. Çıktı 200 karaktere kırpılır. |
| `launch_app` | `LaunchAppTool` / `terminal_tool.py` | MEDIUM | `app_name` | `security.yaml:known_applications` **allowlist**'inden çözülen komutu başlatır. LLM asla keyfi binary/path çalıştıramaz; eşleşme yoksa `subprocess` hiç çağrılmaz. Çözülmüş path TTS'e/kullanıcıya gösterilmez. |
| `get_system_info` | `SystemInfoTool` / `system_info.py` | LOW | — | `core/telemetry.py` ile CPU/RAM/GPU'yu tek cümlede özetler. |
| `media_play_pause` | `MediaPlayPauseTool` / `media_tool.py` | LOW | — | `ctypes.windll.user32.SendInput` ile `VK_MEDIA_PLAY_PAUSE` (OS seviyesi, uygulamadan bağımsız TOGGLE). Kesin durum iddia etmez. |
| `media_next_track` | `MediaNextTrackTool` | LOW | — | `VK_MEDIA_NEXT_TRACK` — kuyrukta ilerler (şarkı adı yoksa). |
| `media_previous_track` | `MediaPreviousTrackTool` | LOW | — | `VK_MEDIA_PREV_TRACK`. |
| `media_volume_up` | `MediaVolumeUpTool` | LOW | — | `VK_VOLUME_UP`. |
| `media_volume_down` | `MediaVolumeDownTool` | LOW | — | `VK_VOLUME_DOWN`. |
| `search_music` | `SearchMusicTool` / `media_tool.py` | LOW | `query` | İki katmanlı: (a) `spotify_search.find_track_id()` (Client Credentials / app-only Spotify API — kişisel OAuth YOK) ile ID bulup `spotify:track:<id>` URI'siyle **gerçek otomatik çalma**; (b) API yoksa/başarısızsa `spotify:search:` ile arama açar. Hep `os.startfile()` (shell yok). |

`_INPUT_UNION` (media_tool) not: Windows `INPUT` union'ı `MOUSEINPUT`/
`KEYBDINPUT`/`HARDWAREINPUT` üçünü de içermeli, yoksa `SendInput` sessizce
0 döner ve tuş fiilen gitmez.

### 8.4 `HANDLERS` vs `TOOL_REGISTRY` (`core/handlers.py`)

`HANDLERS: dict[str, Callable[[Intent], tuple[str, str]]]` — **dosya/sistem
erişimi gerektirmeyen**, LLM'e hiç gitmeyen intent'ler. Şu an sadece
`get_time` (`_handle_get_time` → `datetime.now()` + dile göre şablon).
Handler'ı olmayan bir intent `core.app`'te otomatik olarak chat'e düşer.

### 8.5 Tool çalıştırma hattı — `core/app.py`

`_execute_tool()` → `_run_tool_pipeline()`'i `hud_bus.publish_tool("start"/"end")`
ile bir `try/finally` içinde sarar (hangi return yolundan çıkılırsa çıkılsın
tam bir start + bir end garanti).

`_run_tool_pipeline()` — **güvenlik kararının tek merkezi**, sıra:

1. **`on_start()`** — spinner'ı durdur (onay paneli rich Live render'ıyla
   çakışmasın — security-reviewer bulgusu).
2. **Girdi guardrail'i** — `lang` hariç **TÜM** parametre değerleri
   `_OUTPUT_GUARDRAIL.run(value)` (`OutputSafetyCheck`). Biri yıkıcı
   kalıba takılırsa (`rm -rf`, `format`, `DROP TABLE`, LOLBAS...) → **onay
   bile sorulmadan** ret mesajı döner (defense-in-depth: yanlışlıkla "Y"
   ihtimali bu kalıplar için doğmaz).
3. **Risk onayı** — `requires_approval(tool.risk_level)` (LOW dışı her şey):
   - `speak(_APPROVAL_PENDING_MESSAGES)` — kullanıcı ekrana bakmıyor olabilir.
   - `print_approval_panel(tool.name, risk, risky_values)` — LLM'in
     ürettiği **tam** parametreyi büyük ve net gösterir (kullanıcı kendi
     söylediğinden farklı olsa bile GÖRÜR).
   - Hibrit modda: `print_approval_prompt(...)` +
     `input_hub.wait_for_text_answer(pending)` → `evaluate_approval_answer()`.
     Aksi halde `request_approval()` (kendi `console.input()`'u).
   - **Varsayılan RED** — boş/tanınmayan cevap, `EOFError`, `/`-komut → hepsi "hayır".
4. **Çalıştır** — `tool.execute(intent.parameters)` `try/except` içinde
   (bir kötü tool çağrısı döngüyü çökertmez).
5. **Çıktı guardrail'i** — `_OUTPUT_GUARDRAIL.run(result)` (özellikle MCP'nin
   dış/güvenilmeyen dönüş değeri için; yerel araçların dönüşü zaten temiz).
6. `result` döner → `_handle_turn` onu `(result, lang)` olarak yield eder → TTS.

`/test <araç> [key=value ...]` (cli_commands) dispatcher'ı **bypass** eder
ama **bu hattı bypass etmez** — aynı guardrail/risk/onay + `validate_arguments()`.

---

## 9. Risk & Onay — `core/risk.py`

```python
class RiskLevel(Enum):
    LOW      # salt-okunur → onaysız çalışır
    MEDIUM   # kalıcı değişiklik → [Y/N] onay
    HIGH     # keyfi/yıkıcı olabilir → [Y/N] onay
    CRITICAL # kök dizin/donanım → RFID fiziksel onay (henüz hiçbir tool kullanmıyor)
```

- `requires_approval(level)` → `level is not RiskLevel.LOW`.
- `request_approval(prompt)` — bloklayıcı `console.input("... [Y/N]: ")`,
  `_AFFIRMATIVE = {"y","yes","e","evet"}`, `EOFError` → `False`.
- `evaluate_approval_answer(answer)` — dışarıdan okunmuş cevaba aynı kuralı
  uygular (hibrit girdi; stdin'i kendisi okumaz).
- Zero-Trust: **varsayılan HER ZAMAN RED**. Sesli onay bilinçli olarak
  devre dışı (STT yanlış-algılama payı güvenlik-kritik yolda kabul edilemez).

---

## 10. Guardrail — Chain of Responsibility (`core/guardrail/`)

```python
GuardrailChain([check1, check2, ...]).run(text) -> GuardrailResult(allowed, reason, check_name)
# sırayla çalışır, ilk RED'de durur; her karar loglanır + print_guardrail() ile basılır
```

| Check | Dosya | Tarar | Kalıplar (regex, zaten IGNORECASE) |
|---|---|---|---|
| `InputInjectionCheck` | `input_checks.py` | **Girdi** (OWASP LLM01) | "ignore previous instructions", "you are now a", "reveal your system prompt", "DAN mode", "önceki talimatları yok say", "sistem promptunu göster"... |
| `OutputSafetyCheck` | `output_checks.py` | **Çıktı + tool parametreleri** (OWASP LLM02) | `rm -rf`, `mkfs`, fork bomb, `del/rd /s/q`, `format X:`, `diskpart`, `reg delete`, `takeown`/`icacls`, `shutdown`, `Remove-Item -Recurse/-Force`, `-EncodedCommand`, `iex`/`Invoke-Expression`, `curl \| sh`, `certutil -urlcache`, `netsh ... firewall`, `DROP TABLE/DATABASE`, `mshta`, `regsvr32 /i:`, `rundll32`, `wmic /format`, `bitsadmin /transfer`, `schtasks /create`, `vssadmin delete shadows`, `wbadmin delete`, `wevtutil cl`, `net user /add`, `net localgroup administrators`... |

Kullanımı (`core/app.py`):
- `_INPUT_GUARDRAIL = GuardrailChain([InputInjectionCheck()])` — her turun başında.
- `_OUTPUT_GUARDRAIL = GuardrailChain([OutputSafetyCheck()])` — Brain'in her
  cümlesinde + her tool parametresinde + her tool dönüşünde.

**Önemli:** Bu bir sanitizer değil, **ilk eleme** katmanıdır — kapsamlı kara
liste imkânsız; gerçek güvence her zaman insan onayıdır.

---

## 11. Çıktı Katmanı

### 11.1 Mouth — `mouth/tts.py`

- Model: **Coqui XTTS-v2** (`tts_models/multilingual/multi-dataset/xtts_v2`),
  zero-shot voice cloning. `COQUI_TOS_AGREED=1` (CPML lisansı, bilinçli
  ticari-olmayan kullanım kararı).
- **Tek model instance**, dile göre iki referans embedding çifti:
  - `jarvis_reference.wav` (EN, **zorunlu** — yoksa `FileNotFoundError`).
  - `jarvis_reference_tr.wav` (TR, opsiyonel — yoksa EN'e düşer, VRAM-nötr).
  - Profil = `(gpt_cond_latent, speaker_embedding)` — birkaç MB.
- `speak(text, language=None, stop_event=None, speaking_event=None)`:
  - `lang = language or detect_language(text)`; `tr` → TR profili, aksi
    halde EN profili (fonetik `lang` tüm dilleri kapsar ama klonlanan ses
    sadece EN/TR arasında switch eder).
  - **Producer/consumer:** `_produce_tts_chunks` ayrı thread'te
    `model.inference_stream(...)` çalıştırıp chunk'ları `queue.Queue`'ya
    (maxsize 8) koyar; `speak()` ana thread'de kuyruktan okuyup
    `sd.OutputStream`'e yazar. **Diske .wav yazılmaz.** Jitter buffer
    (`TTS_PREBUFFER_CHUNKS = 3`) ilk chunk yavaşlığını yutar.
  - `_PLAYBACK_LOCK` — iki `speak()` sesi asla üst üste binmez.
  - `stop_event` — 3 noktada kontrol; kapanışta `out.abort()` (Pa_AbortStream,
    kalan sesi beklemeden durur — `close()` beklerdi, shutdown ~3 sn sürerdi).
  - `speaking_event` — oynatmadan hemen önce `set()`, bitince
    `MIC_MUTE_COOLDOWN_S = 0.5` sn sonra `clear()` (`try/finally` garanti).
    Amaç: mikrofonun Jarvis'in kendi sesini yeni tur sanıp aynı aracı
    tekrar tetiklemesini önlemek (AEC yok).
  - `hud_bus.publish_state("speaking")` / `"idle"`.
- CUDA/CPU fallback deseni (`_load_tts_model_with_fallback()`); `torch`
  Windows wheel'i CUDA DLL'lerini taşıdığından `os.add_dll_directory` hack'i
  gerekmez.
- **Monkeypatch:** `torchaudio.load = _load_audio_via_soundfile` — torch
  2.9+'ta default backend `torchcodec` sistemde FFmpeg ister (yok);
  `soundfile` (libsndfile) ile aynı `(tensor, sample_rate)` sözleşmesi
  taklit edilir. `torchcodec` pip paketi yine de kurulu kalmalı
  (`TTS/__init__.py` import zamanında arıyor).

### 11.2 Dil tespiti — `core/language.py`

`detect_language(text, default="en")` — `langdetect`; boş/desteklenmeyen →
`default`. `SUPPORTED_LANGUAGES` = XTTS'in desteklediği 17 kod. Hem `core`
hem `mouth` bu tek paylaşılan mantığı kullanır (iki kopya sapmasın diye).

---

## 12. MCP Bilgi Katmanı (opsiyonel, fail-soft)

**İlke (hibrit):** MCP **yalnızca bilgi/veri erişimi** için; OS kontrolü
(terminal, uygulama, medya) **asla** MCP üzerinden geçmez — yerel
`TOOL_REGISTRY`/`security.yaml` sandbox'ında sabit kalır.

- `core/mcp_config.py` — `config/mcp_servers.yaml` (kişisel, gitignore'da;
  şablon: `.example.yaml`). **FAIL-SOFT:** dosya yok/boş/parse edilemez →
  boş liste + uyarı, uygulama çalışmaya devam eder. MCP araçlarına asla
  `LOW` risk verilmez (dış/güvenilmeyen veri) — istense bile `MEDIUM`'a çekilir.
- `adapters/mcp_client_adapter.py:MCPClientAdapter` — `mcp` SDK asyncio
  tabanlı, proje senkron. **Async↔sync köprüsü:** her sunucu bir kez
  başlatılıp kalıcı arka-plan thread'indeki tek event-loop'ta canlı tutulur;
  senkron `call_tool()` → `asyncio.run_coroutine_threadsafe()`. Modül-seviyesi
  Singleton (`get_default_adapter()`).
  - `_serve()` — tek uzun-ömürlü coroutine: bağlan + keşfet + `shutdown()`
    beklenene kadar bekle (anyio cancel-scope: aynı Task'ta aç/kapa zorunlu).
  - `_wrap_mcp_tool()` — keşif anında sunucunun `name`/`description`/param
    açıklamalarını `InputInjectionCheck`'ten geçirir ("tool poisoning" /
    rug-pull). Takılan araç sessizce atlanır.
  - `allowed_tools` allowlist hem keşifte hem `call_tool()`'da (çift katman).
  - `_CALL_TIMEOUT_SECONDS = 30` — `asyncio.wait_for` ile iptal garanti
    (donmuş sunucuda Task sızıntısı yok).
- `tools/mcp_tool.py:MCPTool` — `Tool(ABC)` sarmalayıcı, `call_fn`
  constructor injection (gerçek MCP sunucusu olmadan test edilebilir).
  - `execute()` — `lang`'i süzer, `call_fn(forwarded)` çağırır, **RAW
    içeriği** konsola basmadan/kırpmadan önce `OutputSafetyCheck`'ten
    geçirir (security-reviewer bulgusu — eskiden placeholder metin
    taranıyordu). TTS'e ≤300 karakter ise tam sonuç, değilse "terminale
    baktım" özeti; tam sonuç `print_mcp_result()` ile panelde.
  - Ad şeması: `mcp_<sunucu>_<araç>` (çakışma önleme + köken şeffaflığı).

---

## 13. JARVIS HUD — Web Arayüzü

Ana döngü ile HUD arasındaki **tek ortak durum** `core/hud_bus.py`
(thread-safe pub/sub). Geri kalan hiçbir modül asyncio bilmez.

- **`core/hud_bus.py`** — "sync thread → N asyncio loop" köprüsü.
  `publish(event)` herhangi bir sync thread'den çağrılır; abone yoksa ucuz
  no-op (sadece 100'lük log ring-buffer + son state güncellenir). Abone
  varsa `loop.call_soon_threadsafe(queue.put_nowait, event)` (doğrudan
  `put_nowait` race'e açık). Tipli yardımcılar: `publish_log`,
  `publish_state` (`idle`/`listening`/`processing`/`speaking`),
  `publish_telemetry`, `publish_tool` (`start`/`end`).
- **`core/api.py`** — FastAPI + `/ws` WebSocket, **ayrı daemon thread**'te
  uvicorn (port 8000). Güvenlik: (1) sadece `127.0.0.1`, (2) WebSocket
  handshake'inde elle `Origin` doğrulaması (`_ALLOWED_ORIGINS` =
  localhost:5173) — `CORSMiddleware` WS için yetersiz (DNS-rebinding /
  CSRF-benzeri saldırıya karşı). Bağlantıda `snapshot` (state + son loglar
  + statik sistem bilgisi) yollar; `_receive_loop` gelen metni
  `hub.submit_external_text()` ile ana kuyruğa koyar; `_send_loop` hub
  olaylarını yayınlar. `_lifespan` içinde saniyede 1 telemetri pushu
  (`asyncio.to_thread(read_system_telemetry)` — `psutil.cpu_percent`
  bloklayıcı).
- **`core/telemetry.py`** — `psutil` (+ opsiyonel `nvidia-smi`) tek
  noktadan. `SystemInfoTool` VE HUD aynı fonksiyonları kullanır (DRY).
  "Sahte veri yok" ilkesi: gerçek karşılığı olmayan alan (latency, sıcaklık)
  gösterilmez.
- **`core/web_ui_process.py`** — `web-ui/`de `npm run dev` (Vite, port 5173)
  alt-süreç yönetimi; Windows `.cmd` sarmalayıcı tuzağı için `taskkill /T /F`.
- **`web-ui/`** — React + TS + Vite + three.js HUD (holografik "NeuralCore"
  küre + terminal aynası + telemetri göstergeleri). Ayrıca kökte `UI/` ve
  `web-ui/` yanında referans HTML tasarımı.

---

## 14. Geliştirici (Slash) Komutları — `core/cli_commands.py`

`/` ile başlayan her metin buraya; Guardrail/Dispatcher/Brain/TTS'e
uğramaz, **sesli yanıt üretmez** (sadece `core/console.py`).

| Komut | İşlev |
|---|---|
| `/help` | Komut tablosu + tüm araçların (yerel + MCP) `ad · açıklama · risk` tablosu. |
| `/status` | Ears/Brain/Mouth cihazı (cuda/cpu), debug modu, hafıza doluluğu (`n/12`), aktif araç listesi. |
| `/debug` | Root logger seviyesini `DEBUG`↔`INFO` toggle (guardrail süreleri, router ham `tool_calls`). |
| `/clear` | `history`'yi system prompt'a sıfırla + ekranı temizle. |
| `/test <araç> [key=value ...]` | Dispatcher'ı bypass edip aracı doğrudan çalıştır — **ama** risk/onay/guardrail + `validate_arguments()` yine çalışır. Tehdit modeli: "konsola erişen = terminal-eşdeğeri güven" (tek-kullanıcılı). |
| `/exit` | `stop_event.set()` (Ctrl+C ile aynı). |

---

## 15. Konsol / Loglama — `core/console.py`

"Tüm terminal çıktısı buradan geçmeli" ilkesi. `setup_logging()` tek
merkezi `RichHandler` (idempotent — ilk çağıran kazanır). Her `print_*`
fonksiyonu ayrıca `hud_bus.publish_log(...)` çağırır → web arayüzü, onlarca
çağrı noktasına dokunmadan aynı çıktıyı görür.

- `print_system` (info/success/warning/error), `print_agent` (diyalog),
  `print_guardrail`, `print_approval_panel`, `print_approval_prompt`,
  `print_router_decision`, `print_mcp_result`, `print_table`, `print_panel`,
  `status_spinner`, `print_boot_sequence`.
- **Sadece 7-bit ASCII** (Windows konsol kod sayfası cp1254 Unicode
  sembolleri kapsamaz — `[OK]`/`[!]`/`[X]` gibi işaretler).
- Kullanıcıdan/dışarıdan gelen her string `rich.markup.escape()` +
  (MCP için) `_strip_control_characters()` (ANSI/ESC enjeksiyonu).

---

## 16. Güvenlik Yapılandırması — `core/security_config.py` + `config/`

- `config/security.yaml` (kişisel, gitignore'da; şablon:
  `security.example.yaml`) — **fail-loud:** dosya yoksa `FileNotFoundError`
  (her tool çağrısının ön koşulu).
  - `allowed_directories`, `known_applications` (ad→komut allowlist),
    `obsidian_vault`.
- `is_path_safe(path)` — `Path.resolve()` + `is_relative_to()` (string-prefix
  DEĞİL; symlink/`../` kaçışı normalize edilir). **Kapsam uyarısı:** şu an
  sadece kod-sabit yollarla çağrılıyor; LLM'in ürettiği bir yol doğrudan
  buraya geçerse UNC/`\\?\` reddi + dosya-adı allowlist EKSİK.
- `resolve_app_command(name)`, `get_obsidian_vault()`.
- `core/paths.py:PROJECT_ROOT` — kaynak ağacından türetilen, CWD-bağımsız
  mutlak kök (araçlar servis/zamanlanmış görev olarak farklı dizinden
  başlatılırsa yanlış yere yazmasın).

---

## 17. Dosya Yapısı

```
main.py                     # ince giriş noktası: alt sistemleri yükle → run_jarvis()
system_prompt.txt           # Brain persona/kuralları (kod dışı, düzenlenebilir)
jarvis_reference.wav        # Mouth EN referans sesi (ZORUNLU)
jarvis_reference_tr.wav     # Mouth TR referans sesi (opsiyonel)
requirements.txt            # cu128 torch --extra-index-url dahil
CLAUDE.md                   # Claude Code için proje talimatları (şu anki durum özeti)

config/
  security.yaml / .example.yaml       # yerel tool erişim kontrolü (fail-loud)
  mcp_servers.yaml / .example.yaml     # MCP sunucu tanımları (fail-soft)

docs/
  ARCHITECTURE.md           # HEDEF mimari (multi-agent, guardrail, VRAM, MCP §9)
  ROADMAP.md                # Faz 1–6 görev listesi
  ONBOARDING.md, TODO.md, claude-code-rehberi.md, plans/
  mimari-genel-bakis.md     # (bu dosya)

src/jarvis/
  ears/listener.py          # IDLE/ACTIVE/FOLLOWUP state machine + wake-word + VAD + faster-whisper
                            #   + Windows CUDA DLL-fix (modül başı) + CPU/int8 fallback
  brain/llm.py              # Ollama llama3.1:8b, streaming cümle-cümle + history (MAX 12) + hata sınıfları
  mouth/tts.py              # XTTS-v2 çift-dilli, producer/consumer streaming oynatma, torchaudio monkeypatch

  core/
    app.py                  # run_jarvis() — ANA DÖNGÜ. _handle_turn() (karar ağacı),
                            #   _execute_tool()/_run_tool_pipeline() (güvenlik hattı)
    dispatcher.py           # Dispatcher.classify() — fast-path regex + semantic router (Ollama tool-calling)
                            #   Intent, _RULES, _NO_TOOL_SCHEMA, _ROUTER_SYSTEM_PROMPT, SHUTDOWN_INTENT_NAME
    handlers.py             # HANDLERS: LLM'siz intent → (text, lang) (şu an sadece get_time)
    input_hub.py            # InputHub — mic + stdin → tek queue.Queue; wait_for_text_answer() (onay)
    cli_commands.py         # /help /status /debug /clear /test /exit
    risk.py                 # RiskLevel, requires_approval(), request_approval(), evaluate_approval_answer()
    console.py              # merkezi rich konsol + setup_logging() + tüm print_* (+ hud_bus.publish_log)
    language.py             # detect_language() — paylaşılan langdetect sarmalayıcı
    paths.py                # PROJECT_ROOT (CWD-bağımsız)
    text.py                 # strip_trailing_punct() (STT'nin komut sonuna eklediği noktalama)
    security_config.py      # security.yaml okuma + is_path_safe() + resolve_app_command() + enabled_dynamic_agents
    registry_loader.py      # agents/registry/*.yaml → dinamik Tool (allowlist'li, fail-soft, Faz 6.4)
    telemetry.py            # psutil/nvidia-smi — SystemInfoTool + HUD ortak
    hud_bus.py              # thread-safe pub/sub: sync thread → asyncio WebSocket kuyrukları
    api.py                  # FastAPI + /ws (ayrı daemon thread, port 8000, Origin doğrulaması)
    web_ui_process.py       # web-ui Vite dev sunucusu alt-süreci (start/stop + taskkill)
    guardrail/
      base.py               # GuardrailCheck (ABC), GuardrailChain (Chain of Responsibility)
      input_checks.py       # InputInjectionCheck (OWASP LLM01)
      output_checks.py      # OutputSafetyCheck (OWASP LLM02 — ~35 tehlikeli kalıp)

  agents/base.py            # Agent (ABC): respond() + supports_tools() + call_tools(); ToolCall, AgentToolResponse

  adapters/
    agent_factory.py        # AgentFactory.create(role) + Llama/Hermes/ClaudeCode adaptörleri
                            #   + check_ollama_connection()
    tool_schema.py          # Tool → Ollama function-calling şeması + validate_arguments() (fail-closed)
    mcp_client_adapter.py   # MCPClientAdapter — async↔sync köprü, keşif, tool poisoning taraması

  tools/
    base.py                 # Tool (ABC): name/description/risk_level/parameters_schema/execute()
    registry.py             # TOOL_REGISTRY (statik) + all_tools()/get_tool() (statik + dinamik manifest + MCP birleşik view, Faz 6.4)
    notes_tool.py           # CreateNoteTool, ReadNotesTool (Obsidian vault, sabit dosya)
    files.py                # ListFilesTool (jarvis_workspace/ salt-okunur)
    terminal_tool.py        # RunCommandTool (HIGH), LaunchAppTool (MEDIUM, allowlist)
    system_info.py          # SystemInfoTool
    media_tool.py           # Media*Tool (SendInput VK_*), SearchMusicTool
    spotify_search.py       # Client Credentials Spotify API — sadece ad → track ID
    mcp_tool.py             # MCPTool — bir MCP aracının Tool sarmalayıcısı + RAW sonuç guardrail'i

tests/                      # pytest — test_dispatcher_router, test_guardrail, test_tool_schema,
                            #   test_input_hub, test_mcp_tool, test_agent_factory, test_security_config,
                            #   test_media_tool, test_spotify_search, test_cli_commands, test_tools
web-ui/                     # React + TS + Vite + three.js HUD (App.tsx, components/, hooks/, lib/)
jarvis_workspace/           # ListFilesTool'un baktığı izole dizin
```

---

## 18. Önemli Fonksiyonlar — Hızlı Referans

| Fonksiyon | Dosya | Rol |
|---|---|---|
| `run_jarvis()` | `core/app.py` | Ana döngü; InputHub'ı kurar, kuyruktan okur, `_handle_turn` → `speak`. |
| `_handle_turn()` | `core/app.py` | Bir turun karar ağacı: girdi guardrail → dispatcher → (shutdown / handler / tool / chat). Generator, `(text, lang)` yield eder. |
| `_run_tool_pipeline()` | `core/app.py` | **Tek merkezi güvenlik hattı:** param guardrail → risk onayı → execute → çıktı guardrail. |
| `_execute_tool()` | `core/app.py` | `_run_tool_pipeline`'ı HUD start/end yayınlarıyla sarar. |
| `Dispatcher.classify()` | `core/dispatcher.py` | fast-path regex → Ollama tool-calling router → `Intent`. |
| `Dispatcher.match_rule()` | `core/dispatcher.py` | Sadece `_RULES` (get_time, shutdown); LLM'e gitmez. |
| `think_and_respond_stream()` | `brain/llm.py` | Ollama streaming chat, cümle cümle yield, history yönetimi + hata sınıfları. |
| `_trim_history()` | `brain/llm.py` | history'yi 12 mesaja kırpar (system hariç). |
| `AgentFactory.create()` | `adapters/agent_factory.py` | role → `Agent` (orchestrator/tool_agent/deep_reasoning). |
| `LlamaOrchestratorAdapter.call_tools()` | `adapters/agent_factory.py` | `ollama.chat(tools=..., temperature=0.1)` → `AgentToolResponse` (bozuk yanıt = boş liste). |
| `build_ollama_tools()` / `build_function_schema()` | `adapters/tool_schema.py` | `Tool` → Ollama function-calling şeması. |
| `validate_arguments()` | `adapters/tool_schema.py` | Router argümanlarını şemaya karşı doğrular (**fail-closed**; şema dışı anahtar elenir, yanlış tip → tüm çağrı reddedilir). |
| `Tool.execute()` | `tools/*.py` | Aracı çalıştırır, TTS'e kısa cümle döner. |
| `all_tools()` / `get_tool()` | `tools/registry.py` | statik + dinamik manifest + MCP birleşik görünüm (öncelik statik > dinamik > MCP). |
| `load_dynamic_tools()` | `core/registry_loader.py` | `agents/registry/*.yaml` → `Tool`; yalnızca `security.yaml:enabled_dynamic_agents` allowlist'indeki dosya kökleri, fail-soft (Faz 6.4). |
| `load_toolsets()` | `core/registry_loader.py` | `agents/registry/*.toolset.yaml` → `ToolSet` (üye araç adları + orkestrasyon metadatası); `risk_ceiling` yükleme-zamanı kontrolü, allowlist kapılı (Faz 6.10). |
| `_run_delegate_complex()` | `core/app.py` | Genel orkestrasyon döngüsü: tool-set seçimi → kapsamlı şema → sınırlı adım döngüsü (`_MAX_DELEGATE_STEPS` clamp) → her adım `_run_tool_pipeline`'dan geçer. 0 tool-set → düz `all_tools()` (Faz 6.3 + 6.10). |
| `GuardrailChain.run()` | `core/guardrail/base.py` | Check'leri sırayla çalıştırır, ilk RED'de durur. |
| `requires_approval()` / `request_approval()` | `core/risk.py` | LOW dışı → [Y/N]; varsayılan RED. |
| `speak()` | `mouth/tts.py` | XTTS-v2 streaming oynatma; `stop_event`/`speaking_event` kancaları. |
| `listen_loop()` | `ears/listener.py` | IDLE/ACTIVE/FOLLOWUP state machine; transkript generator. |
| `InputHub.next_event()` / `wait_for_text_answer()` | `core/input_hub.py` | Birleşik kuyruktan olay / onay cevabı okuma. |
| `detect_language()` | `core/language.py` | Paylaşılan langdetect sarmalayıcı. |
| `hud_bus.publish_*()` | `core/hud_bus.py` | Sync thread → WebSocket olay yayını. |
| `MCPClientAdapter.discover_tools()` / `call_tool()` | `adapters/mcp_client_adapter.py` | MCP araç keşfi (cache'li) / senkron çağrı köprüsü. |

---

## 19. Eşzamanlılık Modeli (Thread Haritası)

| Thread | Ne çalıştırır | Notlar |
|---|---|---|
| **Ana thread** | `run_jarvis()` döngüsü, `_handle_turn`, tool execute, `speak()` consumer, Brain/router Ollama çağrıları | Tüm karar mantığı burada, sırayla. Bloklayıcı çağrılar Ctrl+C ile yarıda kesilmez. |
| `jarvis-mic` (daemon) | `_mic_producer` → `listen_loop()` | Sürekli açık `sd.InputStream`. `speaking_event` set'ken tetikleme aramaz. |
| `jarvis-text-input` (daemon) | `_text_producer` → `console.input()` | **stdin'in tek sahibi.** |
| `jarvis-scheduler` (daemon, opt-in) | `core/scheduler.py:Scheduler` → cron poll | `config/scheduled_tasks.yaml` varsa. Denk gelince `InputEvent(source="scheduled")`. |
| `jarvis-continuous` (daemon, opt-in) | `core/continuous_runner.py:ContinuousRunner` → koşul poll | Aynı dosya varsa. Dosya mtime değişince `InputEvent(source="continuous")`. |
| TTS producer (daemon, `speak()` başına) | `_produce_tts_chunks` → `model.inference_stream()` | GPU forward pass; chunk'ları kuyruğa. |
| `jarvis-hud-api` (daemon) | `uvicorn.run(app)` — kendi asyncio loop'u | FastAPI `/ws`. Ana thread'i asla bloklamaz. |
| `mcp-client-adapter` (daemon) | Kalıcı asyncio event-loop | MCP oturumları; `run_coroutine_threadsafe` ile köprü. |
| `web-ui` alt-süreci | `npm run dev` (node/vite) | `atexit` + `taskkill /T /F`. |

Thread'ler arası ortak durum: `threading.Event` (`stop_event`,
`speaking_event`), `queue.Queue` (InputHub), `hud_bus` (kilitli pub/sub).

---

## 20. Bilinen Sınırlamalar & İyileştirme Adayları

Bunlar kod yorumlarında/roadmap'te açıkça işaretlenmiş, geliştirme
planlamasının doğal başlangıç noktaları:

1. **Çift LLM çağrısı gecikmesi** — kural eşleşmeyen her turda router +
   (chat'e düşerse) Brain ayrı ayrı çağrılır. Öneri: router+chat'i tek
   çağrıda birleştir veya küçük/hızlı ayrı router modeli.
2. **Sohbet yolu `Agent` arayüzünü kullanmıyor** — `brain/llm.py` doğrudan
   `ollama.chat` streaming; Factory/Adapter soyutlamasının dışında (tarihsel).
   Birleştirme, sağlayıcı değişiminde tek nokta kazandırır.
3. **Multi-agent bağlı değil** — `HermesAgentAdapter.call_tools()` ve tüm
   `ClaudeCodeAdapter` `NotImplementedError`. §4 (docs/ARCHITECTURE.md)
   Orkestratör↔Hermes↔Claude Code delegasyon şeması henüz kağıt üstünde.
4. **Otonom görev zinciri yok** (ROADMAP Faz 6) — her tur tek adım:
   plan→araç→değerlendir→devam döngüsü yok. Tool sonuçları `history`'ye
   girmiyor (bu, indirect-prompt-injection yüzeyini bugün kapatıyor ama
   Faz 6 ile yeniden değerlendirilmeli).
5. **`Tool.execute()` imzasında `stop_event` yok** — bir MCP çağrısı (veya
   herhangi bir yavaş tool) ana döngüyü ~30 sn bloklayabilir; kötü niyetli/
   donmuş bir sunucu bunu tekrarlayabilir. Tüm tool'ları etkileyen bir
   imza değişikliği gerektiriyor.
6. **`is_path_safe()` genel-amaçlı değil** — LLM'in ürettiği bir yol
   parametresi doğrudan geçerse UNC/`\\?\` reddi + dosya-adı allowlist yok.
7. **CRITICAL risk seviyesi kullanılmıyor** — RFID `TrustElevation` modülü
   ve ses biyometrisi (ROADMAP Faz 3.2) henüz yok.
8. **AEC (akustik yankı bastırma) yok** — `speaking_event` mute'u kaba bir
   çözüm; gerçek barge-in için AEC gerekir (MVP dışı bırakılmış).
9. **RunCommandTool `shell=True`** — tanım gereği "kullanıcının dikte ettiği
   komut", ama `&&`/`|`/`;` zincirleme mümkün; savunma = tam metin gösterimi
   + OutputSafetyCheck + zorunlu onay.
10. **Router `no_tool_needed` sentinel'i bir workaround** — Ollama'nın
    sunucu-tarafı "her zaman fonksiyon çağır" şablonuna karşı. Farklı bir
    router mekanizması (structured output, grammar-constrained decoding,
    ayrı sınıflandırma modeli) bunu gereksiz kılabilir.
11. **Guardrail kara listeleri regex** — kapsamlı değil (bilinçli "ilk
    eleme"); sınıflandırma modeli tabanlı bir katman OWASP LLM01/LLM02'yi
    daha iyi karşılar.
12. **VRAM bütçesi** (RTX 4070, 12 GB): Whisper turbo (~1.5–2) + llama3.1:8b
    (~5) + XTTS-v2 (~2–3) ≈ 8.5–10 GB. Ayrı bir Hermes modeli eklemek sınırı
    zorlar → "paylaşımlı model, çoklu persona" veya Ollama `keep_alive` ile
    sıralı yükleme önerisi.

---

## 21. Çalıştırma & Test

```bash
# Kurulum (sıra önemli — CUDA'lı torch PyPI default index'te yok)
venv\Scripts\pip install torch==2.11.0+cu128 torchaudio==2.11.0+cu128 --index-url https://download.pytorch.org/whl/cu128
venv\Scripts\pip install -r requirements.txt
cp config/security.example.yaml config/security.yaml   # + gerçek vault yolu / uygulama komutları

# Ön koşullar: `ollama serve` çalışıyor + `ollama pull llama3.1:8b`

python main.py                              # tam döngü (Ears + terminal + HUD)
python -m src.jarvis.ears.listener          # sadece Ears (tek-atım transkripsiyon)
python -m src.jarvis.adapters.mcp_client_adapter   # MCP keşif doğrulaması
python -m pytest tests/ -v                  # testler (bare `pytest` DEĞİL — import çözümü için `-m`)
```

Doğrulama skill'leri: `verify-audio-pipeline`, `verify-wakeword-pipeline`,
`verify-brain-pipeline`. Debug subagent'ları: `pipeline-debugger`,
`security-reviewer`.
