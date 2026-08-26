# Jarvis — Kişisel Otonom Asistan Sistemi

Modüler, terminal tabanlı, yapay zeka destekli otonom asistan. Amaç: günlük iş
akışlarını optimize etmek, sistem otomasyonları çalıştırmak, yerel/harici
araçları (tool-calling) verimli kullanmak; siber güvenlik ve yazılım geliştirme
süreçlerinde destek vermek.

## Mimari (mevcut durum)

Kod `src/jarvis/` paket hiyerarşisinde (bkz. "Depo Düzeni"); kökte sadece
ince bir `main.py` giriş noktası var.

- **Ears** (`src/jarvis/ears/listener.py`) — `listen_loop()` bir **state
  machine**: IDLE (openWakeWord `hey_jarvis` ile "Hey Jarvis" dinlenir,
  hiçbir şey transkribe edilmez) ↔ ACTIVE (wake-word sonrası `webrtcvad`
  ile VAD-tabanlı dinamik kayıt, disk'e yazmadan ndarray) ↔ FOLLOWUP (bir
  utterance bitince wake-word gerekmeden kısa bir takip penceresi açılır;
  pencere sadece gerçek bir yanıt üretildiğinde sıfırdan yenilenir,
  gürültü kaynaklı boş turlar kalan süreyi tüketir — IDLE'a dönüşü
  süresiz ertelemez) → `faster-whisper` (`turbo` = large-v3-turbo,
  `multilingual=True` + TR/EN karışık `initial_prompt`, CUDA/float16 +
  otomatik CPU/int8 fallback, `vad_filter=True`) ile transkripsiyon.
- **Brain** (`src/jarvis/brain/llm.py`) — transkripti, döngü boyunca
  kalıcı tutulan bir `history` listesiyle (son `MAX_HISTORY_MESSAGES`
  mesaj, eskiler otomatik kırpılır) `ollama.chat(..., stream=True)`
  üzerinden yerel `llama3.1:8b`'ye gönderir; `think_and_respond_stream()`
  tamamlanan her cümleyi ürettikçe `yield` eder (Mouth'a cümle cümle
  beslenir). `SYSTEM_PROMPT` kökteki `system_prompt.txt`'ten okunur,
  yanıtın kullanıcının kullandığı dilde (TR/EN) verilmesini ister. Ollama
  bağlantı/model hataları (`httpx.ConnectError`/`ConnectionError`,
  `ollama.ResponseError` 404) ayrı ayrı yakalanıp net TR/EN mesajla
  bildirilir.
- **Mouth** (`src/jarvis/mouth/tts.py`) — Coqui **XTTS-v2** ile zero-shot
  voice cloning: **tek** model instance'ı, dile göre seçilen iki referans
  ses/embedding çifti tutar (`jarvis_reference.wav` EN zorunlu,
  `jarvis_reference_tr.wav` TR opsiyonel — yoksa EN'e düşer, VRAM-nötr).
  `speak(text, language=None)` çağrıldığında `inference_stream()` ile
  üretilen ses chunk'ları, disk'e `.wav` yazmadan doğrudan
  `sounddevice.OutputStream` ile hoparlöre akıtılır.
- **Semantic Router + Tool Use** (Faz 3.3, canlı döngüye bağlı) —
  `core/dispatcher.py:Dispatcher.classify()` önce `_RULES`'taki tek fast-path
  kurala (`get_time`) bakar, eşleşmezse `AgentFactory.create("orchestrator")`
  (yerel `llama3.1:8b`) ile Ollama **native tool-calling**'i kullanarak
  (şema üretimi `adapters/tool_schema.py`) `tools/registry.py:TOOL_REGISTRY`'den
  bir araç seçer; `core/app.py` seçileni `core/risk.py` risk seviyesine göre
  `[Y/N]` onayından (rich panelleriyle — `core/console.py:print_approval_panel`/
  `print_router_decision`) geçirip çalıştırır. `agents/base.py` (`Agent`:
  `respond()` + `call_tools()`) + `adapters/agent_factory.py`
  (`LlamaOrchestratorAdapter`/`HermesAgentAdapter`/`ClaudeCodeAdapter` —
  sadece ilki `call_tools()`'u gerçekten uyguluyor). `core/guardrail/`
  (Chain-of-Responsibility: `InputInjectionCheck`, `OutputSafetyCheck`) artık
  router'ın ürettiği TÜM tool parametrelerini tarıyor. `core/security_config.py`
  + `config/security.yaml` (kişisel, gitignore'da; şablon: `security.example.yaml`)
  `allowed_directories`/`known_applications`/`obsidian_vault` sağlar.
- `src/jarvis/core/app.py:run_jarvis()` — Ears→Brain→Mouth'u bağlayan
  giriş noktası; kökteki `main.py` sadece bunu çağırır.

Genel ilkeler:
- Modüler ajan mimarisi: her yetenek (ses girişi, komut yönlendirme, araç
  kullanımı, otonom görev zinciri) ayrı bir modül/paket — `src/jarvis/
  {ears,brain,mouth,core,adapters,agents,tools}/` altında.
- Clean Code ve tasarım desenleri esas alınır; kısayol/geçici çözüm eklenirken
  neden geçici olduğu koda yorum olarak düşülür.
- Ortam: Windows + Claude Code CLI (Git Bash), izole çalışma alanları için
  git worktree kullanılabilir (bkz. rehberdeki "Worktree" bölümü).

## MVP Yol Haritası

Kısa özet — **alt adımlı detaylı versiyon için `docs/ROADMAP.md`'ye bak.**

1. **Ears** — mikrofon → metin (durum: tamam — IDLE/ACTIVE/FOLLOWUP state
   machine + wake-word + CPU fallback + latency profiling, gerçek mikrofonla
   doğrulandı)
2. **Brain** — transkript → LLM yanıtı (durum: tamam — streaming + hafıza,
   `src/jarvis/brain/llm.py`)
3. **Mouth / TTS** — yanıt → sesli çıktı (durum: tamam — XTTS-v2 çift-dilli
   voice cloning, `src/jarvis/mouth/tts.py`)
4. **Modüler Komut Yöneticisi** (durum: tamam — intent dispatcher [fast-path
   regex + semantic router] + risk-onaylı `TOOL_REGISTRY` çalıştırma, canlı
   döngüye bağlı)
5. **Sistem Entegrasyonları / Tool Use** (durum: tamam, yerel/API'siz — Obsidian
   not, dosya listeleme, terminal/uygulama başlatma, sistem izleme, medya kontrolü)
6. **Otonom Ajan Döngüsü** — ileri düzey görev zincirleri (durum: bekliyor)

## Komutlar

- Kurulum (sıra önemli — CUDA'lı `torch` PyPI'nin varsayılan index'inde
  yok, atlanırsa XTTS sessizce CPU-only'e düşüp çok yavaşlar):
  1. `venv\Scripts\pip install torch==2.11.0+cu128 torchaudio==2.11.0+cu128 --index-url https://download.pytorch.org/whl/cu128`
  2. `venv\Scripts\pip install -r requirements.txt`
  3. `config/security.example.yaml`'ı `config/security.yaml` olarak kopyalayıp
     gerçek Obsidian vault yolunuzu/uygulama komutlarınızı girin (bu dosya
     gitignore'da — kişisel/makineye özel yol içerir).
- Çalıştırma (tam döngü): `python main.py`
- Sadece Ears testi: `python -m src.jarvis.ears.listener`
- Test: `python -m pytest tests/ -v` (bare `pytest` değil — `-m` olmadan
  `src.jarvis` import'u çözülmeyebilir, bkz. `tests/test_guardrail.py`)
- Lint/format: `ruff check .` / `black .` (henüz projeye eklenmedi)

## Kod Stili ve Kurallar

- Python: PEP 8, type hints tercih edilir, fonksiyonlar tek sorumluluk
  prensibine uyar (bkz. `.claude/rules/python-style.md`).
- CUDA/GPU'ya bağımlı kod, GPU olmayan ortamda da (CPU fallback) en azından
  hata vermeden davranmalı — `src/jarvis/ears/listener.py:_load_model_with_fallback()`
  bunu karşılıyor (CUDA'da sessiz bir warm-up transkripsiyonuyla gerçek bir
  inference tetikleyip hata olursa CPU/int8'e düşüyor — salt `WhisperModel()`
  constructor'ı CUDA hatasını yakalamaz, hata ilk gerçek çağrıda patlar).
  **Not:** bu makinede `cublas64_12.dll` eksikliği, `src/jarvis/ears/listener.py`
  başındaki Windows DLL-fix (venv'deki `nvidia-cublas-cu12`/
  `nvidia-cudnn-cu12` pip paketlerinin bin/ dizinlerini
  `os.add_dll_directory` ile tanıtıyor) + bu paketlerin kurulmasıyla
  çözüldü — RTX 4070 CUDA hızlanması artık aktif.
- `src/jarvis/mouth/tts.py` da aynı CUDA/CPU fallback deseninde (`_load_tts_model_with_fallback()`),
  ama `torch`'un Windows wheel'i CUDA DLL'lerini kendi içinde taşıdığı için
  (ctranslate2'nin aksine) `os.add_dll_directory` hack'ine ihtiyaç yok.
  XTTS-v2 modeli Coqui'nin CPML (ticari olmayan kullanım) lisansı altında;
  `COQUI_TOS_AGREED=1` ile bu otomatik kabul ediliyor — bilinçli bir
  lisans kararı, değiştirilirse dikkatli olunmalı.
  **Not:** `torch` 2.9+'ta `torchaudio.load()`'ın varsayılan backend'i
  `torchcodec`'e taşındı; `torchcodec` ise sistemde ayrıca kurulu bir
  paylaşımlı FFmpeg kütüphanesi gerektiriyor (bu makinede yok, pip de
  getirmiyor — `torchcodec` kendi `.dll`'lerini sistemin FFmpeg'ine dinamik
  bağlıyor). XTTS referans `.wav`'i okumak için sadece `torchaudio.load()`
  çağırdığından (`get_conditioning_latents` → `load_audio`), `src/jarvis/mouth/tts.py`
  bunu `torchaudio.load = _load_audio_via_soundfile` ile **monkeypatch**
  ediyor — zaten kurulu olan `soundfile` (libsndfile, FFmpeg gerektirmez) ile
  aynı `(tensor, sample_rate)` sözleşmesini taklit ediyor. `torchcodec` pip
  paketi yine de kurulu kalmalı çünkü `TTS/__init__.py` onu import zamanında
  arıyor (kullanılmasa da olmazsa olmaz).
- Gizli bilgiler (API anahtarı, token) asla koda veya commit'e gömülmez;
  `.env` + `.gitignore` kullanılır (`.gitignore`'da `.env` ve `secrets/`
  zaten hariç tutulmuş durumda).

## İş Akışı Kuralları

- Birden fazla dosyayı etkileyen veya belirsiz kapsamlı değişiklikler için
  önce **plan mode** ile keşif yap, planı onayladıktan sonra uygula.
- Kapsamlı bir araştırma/inceleme gerektiğinde (ör. "mevcut intent parsing
  yaklaşımlarını incele") ana konuşmayı şişirmemek için subagent kullan.
- Her yeni modül bittiğinde: testleri çalıştır, mümkünse gerçek ses/metin
  örneğiyle doğrula, sonra commit mesajı yaz.
- `.claude/agents/security-reviewer` subagent'ı, dış dünyaya açık yüzeyler
  (API entegrasyonları, terminal komutu çalıştırma modülü — Faz 3) eklenince
  proaktif olarak kullanılmalı.
- `.claude/agents/pipeline-debugger` subagent'ı Ears/Brain hatalarında
  (CUDA, gecikme, geçici dosya sızıntısı) kullanılır.
- MCP: şu an gerekli değil; Faz 3 (Tool Use) başladığında, Jarvis'in kendi
  tool-calling katmanını MCP standardına uygun tasarlamak değerlendirilebilir
  (bkz. `docs/claude-code-rehberi.md` §6).

## Depo Düzeni

```
main.py             # ince giriş noktası -> src/jarvis/core/app.py:run_jarvis()
system_prompt.txt   # Brain persona/kuralları (kod dışı, düzenlenebilir)
jarvis_reference.wav      # Mouth EN referans sesi (zorunlu)
jarvis_reference_tr.wav   # Mouth TR referans sesi (opsiyonel)
requirements.txt
config/security.example.yaml   # Şablon (commit'lenir); security.yaml gitignore'da
src/jarvis/
├── ears/listener.py          # Ears — sounddevice + faster-whisper + wake-word
├── brain/llm.py              # Brain — Ollama/llama3.1, streaming + hafıza
├── mouth/tts.py               # Mouth — XTTS-v2 çift-dilli TTS
├── core/
│   ├── app.py                  # run_jarvis() — MVP döngüsü (Ears→Brain→Mouth)
│   ├── console.py              # rich tabanlı konsol + onay/router panelleri
│   ├── dispatcher.py           # Intent sınıflandırma (fast-path + semantic router)
│   ├── security_config.py      # security.yaml okuma + is_path_safe()
│   └── guardrail/               # Chain-of-Responsibility I/O kontrolleri
│   ├── risk.py                 # RiskLevel + [Y/N] onay (Zero-Trust, Faz 3)
│   └── paths.py                 # PROJECT_ROOT (CWD-bağımsız mutlak yollar)
├── tools/                       # base(ABC)+registry, notes_tool (Obsidian),
│                                #  files, terminal_tool (run_command HIGH +
│                                #  launch_app), system_info, media_tool
├── adapters/
│   ├── agent_factory.py         # AgentFactory + Llama/Hermes/ClaudeCode adaptörleri
│   └── tool_schema.py            # Tool -> Ollama function-calling şeması
└── agents/base.py               # Agent (ABC): respond() + call_tools()
.claude/            # Claude Code yapılandırması (agents, rules, skills, hooks)
docs/               # ROADMAP.md, ARCHITECTURE.md, claude-code-rehberi.md
```
Henüz eklenmemiş, ARCHITECTURE.md §7'de tanımlı: `src/jarvis/{security,iot}/`
(Faz 3.2 RFID/ses biyometrisi ve Faz 5 IoT).

## Notlar

- Bu dosya 200 satırın altında tutulmalı; ayrıntılı/uzun materyal
  `.claude/skills/` veya `docs/` içine taşınmalı.
- Kullanım rehberi (skills, subagents, hooks, MCP, izin modları, worktree)
  için `docs/claude-code-rehberi.md`'ye bak.
- Hedef mimari (multi-agent, guardrail, VRAM optimizasyonu, design pattern'lar)
  için `docs/ARCHITECTURE.md`'ye bak — bu dosya (CLAUDE.md) sadece şu anki
  duruma odaklanır.
- `/init` komutunu ileride tekrar çalıştırıp Claude'un kod tabanından
  çıkardığı ek kuralları bu dosyaya entegre edebilirsin.
- TODO: `tests/fixtures/` altına gerçek bir örnek `.wav` + beklenen
  transkript eklenmeli (`verify-audio-pipeline` skill'i bunu kullanacak).
- Kod yazarken, sıradan olmayan her mimari kararın yanına kısa bir yorum
   satırı bırak: neden bu yaklaşım, hangi alternatif elendi.