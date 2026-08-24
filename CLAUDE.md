# Jarvis — Kişisel Otonom Asistan Sistemi

Modüler, terminal tabanlı, yapay zeka destekli otonom asistan. Amaç: günlük iş
akışlarını optimize etmek, sistem otomasyonları çalıştırmak, yerel/harici
araçları (tool-calling) verimli kullanmak; siber güvenlik ve yazılım geliştirme
süreçlerinde destek vermek.

## Mimari (mevcut durum)

Şu an düz kökte iki modül var (henüz `src/` paketlenmesi yok, bkz. "Depo
Düzeni"):

- **Ears** (`audio_handler.py`) — mikrofondan 5 sn kayıt (`sounddevice`) →
  geçici `.wav` → `faster-whisper` (`base` model, CUDA/float16, RTX 4070)
  ile transkripsiyon → geçici dosya temizliği.
- **Brain** (`main.py`) — transkripti `ollama` üzerinden yerel `llama3.1`
  modeline gönderir (bkz. `SYSTEM_PROMPT`), yanıtı konsola yazdırır.
  `run_jarvis()` giriş noktası: Ears → Brain → (şimdilik) print.
- **Mouth (TTS)** — henüz kod yok, `main.py` yorumunda planlanmış; venv'de
  `edge-tts` zaten kurulu (`requirements.txt`) — muhtemel seçim bu.

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

1. **Ears** — mikrofon → metin (durum: MVP tamam, iyileştirme aşamasında)
2. **Brain** — transkript → LLM yanıtı (durum: MVP tamam, `main.py`'de)
3. **Mouth / TTS** — yanıt → sesli çıktı (durum: başlanmadı, sıradaki)
4. **Modüler Komut Yöneticisi** — intent parsing → ilgili modüle yönlendirme
5. **Sistem Entegrasyonları / Tool Use** — dosya yönetimi, terminal komutları,
   sistem izleme, API entegrasyonları
6. **Otonom Ajan Döngüsü** — ileri düzey görev zincirleri

## Komutlar

- Kurulum: `venv\Scripts\pip install -r requirements.txt`
- Çalıştırma (tam döngü): `python main.py`
- Sadece Ears testi: `python audio_handler.py`
- Test: `pytest` (henüz test yok — `tests/` klasörü kurulacak)
- Lint/format: `ruff check .` / `black .` (henüz projeye eklenmedi)

## Kod Stili ve Kurallar

- Python: PEP 8, type hints tercih edilir, fonksiyonlar tek sorumluluk
  prensibine uyar (bkz. `.claude/rules/python-style.md`).
- CUDA/GPU'ya bağımlı kod, GPU olmayan ortamda da (CPU fallback) en azından
  hata vermeden davranmalı — **mevcut `audio_handler.py`'de `device="cuda"`
  sabit kodlanmış, bu kural henüz karşılanmıyor**, ilk fırsat modülerleşme/
  Ears iyileştirmesinde düzeltilmeli.
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
