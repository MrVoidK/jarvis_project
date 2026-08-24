# Jarvis — Kişisel Otonom Asistan Sistemi

Modüler, terminal tabanlı, yapay zeka destekli otonom asistan. Amaç: günlük iş
akışlarını optimize etmek, sistem otomasyonları çalıştırmak, yerel/harici
araçları (tool-calling) verimli kullanmak; siber güvenlik ve yazılım geliştirme
süreçlerinde destek vermek.

## Mimari (mevcut durum)

Şu an düz kökte iki modül var (henüz `src/` paketlenmesi yok, bkz. "Depo
Düzeni"):

- **Ears** (`audio_handler.py`) — `listen_loop()` bir **state machine**:
  IDLE (openWakeWord `hey_jarvis` ile "Hey Jarvis" dinlenir, hiçbir şey
  transkribe edilmez) ↔ ACTIVE (wake-word sonrası `webrtcvad` ile
  VAD-tabanlı dinamik kayıt, disk'e yazmadan ndarray) → `faster-whisper`
  (`turbo` model = large-v3-turbo, `multilingual=True` + TR/EN karışık
  `initial_prompt` ile serbest dil algılama, CUDA/float16 + otomatik
  CPU/int8 fallback, `vad_filter=True`) ile transkripsiyon. Wake-word tespiti
  ve transkripsiyon için latency loglanıyor (`logger.info`).
- **Brain** (`main.py`) — transkripti `ollama` üzerinden yerel
  `llama3.1:8b` modeline gönderir (bkz. `SYSTEM_PROMPT`), yanıtı konsola
  yazdırır. `run_jarvis()` giriş noktası: Ears → Brain → (şimdilik) print.
- **Mouth** (`tts_handler.py`) — Coqui **XTTS-v2** ile zero-shot voice
  cloning: proje kökündeki `jarvis_reference.wav` referans alınarak,
  `speak(text)` çağrıldığında model+konuşmacı embedding'leri (modül
  importunda bir kez yüklenir) üzerinden `inference_stream()` ile üretilen
  ses chunk'ları, disk'e `.wav` yazmadan doğrudan `sounddevice.OutputStream`
  ile hoparlöre akıtılır. `edge-tts` (bulut tabanlı) yerine bu tercih
  edildi — tüm pipeline'ın yerelde/offline çalışması ilkesiyle tutarlı.

Genel ilkeler:
- Modüler ajan mimarisi: her yetenek (ses girişi, komut yönlendirme, araç
  kullanımı, otonom görev zinciri) ayrı bir modül/paket olmalı — büyüdükçe
  `src/jarvis/{ears,brain,mouth,core,tools,agents}/` altına taşınacak.
- Clean Code ve tasarım desenleri esas alınır; kısayol/geçici çözüm eklenirken
  neden geçici olduğu koda yorum olarak düşülür.
- Ortam: Windows + Claude Code CLI (Git Bash), izole çalışma alanları için
  git worktree kullanılabilir (bkz. rehberdeki "Worktree" bölümü).

## MVP Yol Haritası

Kısa özet — **alt adımlı detaylı versiyon için `docs/ROADMAP.md`'ye bak.**

1. **Ears** — mikrofon → metin (durum: IDLE/ACTIVE state machine + wake-word
   `openWakeWord`/"Hey Jarvis" + CPU fallback + latency profiling tamam)
2. **Brain** — transkript → LLM yanıtı (durum: MVP tamam, `main.py`'de)
3. **Mouth / TTS** — yanıt → sesli çıktı (durum: MVP tamam, XTTS-v2 voice
   cloning ile, `tts_handler.py`)
4. **Modüler Komut Yöneticisi** — intent parsing → ilgili modüle yönlendirme
5. **Sistem Entegrasyonları / Tool Use** — dosya yönetimi, terminal komutları,
   sistem izleme, API entegrasyonları
6. **Otonom Ajan Döngüsü** — ileri düzey görev zincirleri

## Komutlar

- Kurulum (sıra önemli — CUDA'lı `torch` PyPI'nin varsayılan index'inde
  yok, atlanırsa XTTS sessizce CPU-only'e düşüp çok yavaşlar):
  1. `venv\Scripts\pip install torch==2.11.0+cu128 torchaudio==2.11.0+cu128 --index-url https://download.pytorch.org/whl/cu128`
  2. `venv\Scripts\pip install -r requirements.txt`
- Çalıştırma (tam döngü): `python main.py`
- Sadece Ears testi: `python audio_handler.py`
- Test: `pytest` (henüz test yok — `tests/` klasörü kurulacak)
- Lint/format: `ruff check .` / `black .` (henüz projeye eklenmedi)

## Kod Stili ve Kurallar

- Python: PEP 8, type hints tercih edilir, fonksiyonlar tek sorumluluk
  prensibine uyar (bkz. `.claude/rules/python-style.md`).
- CUDA/GPU'ya bağımlı kod, GPU olmayan ortamda da (CPU fallback) en azından
  hata vermeden davranmalı — `audio_handler.py:_load_model_with_fallback()`
  bunu karşılıyor (CUDA'da sessiz bir warm-up transkripsiyonuyla gerçek bir
  inference tetikleyip hata olursa CPU/int8'e düşüyor — salt `WhisperModel()`
  constructor'ı CUDA hatasını yakalamaz, hata ilk gerçek çağrıda patlar).
  **Not:** bu makinede `cublas64_12.dll` eksikliği, `audio_handler.py`
  başındaki Windows DLL-fix (venv'deki `nvidia-cublas-cu12`/
  `nvidia-cudnn-cu12` pip paketlerinin bin/ dizinlerini
  `os.add_dll_directory` ile tanıtıyor) + bu paketlerin kurulmasıyla
  çözüldü — RTX 4070 CUDA hızlanması artık aktif.
- `tts_handler.py` da aynı CUDA/CPU fallback deseninde (`_load_tts_model_with_fallback()`),
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
  çağırdığından (`get_conditioning_latents` → `load_audio`), `tts_handler.py`
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
  (API entegrasyonları, terminal komutu çalıştırma modülü — adım 5) eklenince
  proaktif olarak kullanılmalı.
- `.claude/agents/pipeline-debugger` subagent'ı Ears/Brain hatalarında
  (CUDA, gecikme, geçici dosya sızıntısı) kullanılır.
- MCP: şu an gerekli değil; adım 5 (Tool Use) başladığında, Jarvis'in kendi
  tool-calling katmanını MCP standardına uygun tasarlamak değerlendirilebilir
  (bkz. `docs/claude-code-rehberi.md` §6).

## Depo Düzeni

Şu an düz kökte (henüz `src/` yok):
```
main.py            # Brain — Ollama/llama3.1
audio_handler.py   # Ears — sounddevice + faster-whisper
requirements.txt
.claude/           # Claude Code yapılandırması (agents, rules, skills, hooks)
docs/              # ROADMAP.md, claude-code-rehberi.md
```
Modülerleşme (adım 4 — Komut Yöneticisi — başlarken önerilir):
```
src/jarvis/
├── ears/           # ses yakalama + faster-whisper pipeline
├── brain/          # Ollama/LLM katmanı
├── mouth/          # TTS
├── core/           # komut yöneticisi, intent parsing
├── tools/          # tool-calling entegrasyonları
└── agents/         # otonom görev zinciri mantığı
```

## Notlar

- Bu dosya 200 satırın altında tutulmalı; ayrıntılı/uzun materyal
  `.claude/skills/` veya `docs/` içine taşınmalı.
- Kullanım rehberi (skills, subagents, hooks, MCP, izin modları, worktree)
  için `docs/claude-code-rehberi.md`'ye bak.
- `/init` komutunu ileride tekrar çalıştırıp Claude'un kod tabanından
  çıkardığı ek kuralları bu dosyaya entegre edebilirsin.
- TODO: `tests/fixtures/` altına gerçek bir örnek `.wav` + beklenen
  transkript eklenmeli (`verify-audio-pipeline` skill'i bunu kullanacak).
- Kod yazarken, sıradan olmayan her mimari kararın yanına kısa bir yorum
   satırı bırak: neden bu yaklaşım, hangi alternatif elendi.