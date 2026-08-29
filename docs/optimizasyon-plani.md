# Jarvis — Optimizasyon & Bugfix Takip Listesi

Bu dosya, `docs/ROADMAP.md`'den **ayrı** bir canlı listedir: yeni özellik
planlamaz, yalnızca **performans/kararlılık sorunları ve bug'ları** session-arası
takip eder. Yeni bir sorun bulundukça `## Yeni bulgular (backlog)` bölümüne
eklenir; bir madde bitince durum güncellenir ve commit ref yazılır.

Durum lejantı: ⬜ bekliyor · 🟡 kısmen · ✅ tamam

---

## Kaynak: 2026-08-29 canlı test (`python main.py`, 11:43–11:50 logu)

Kullanıcı gözlemleri: (1) Jarvis kendi cevabını duyup kullanıcı söylemiş sanıyor,
(2) cevaplar 20–30 s gecikmeli, (3) ses aç/kıs kademesi hep aynı/belirsiz,
(4) çoklu adım planı yapamıyor, "hantal". Log analiziyle kök nedenlere bağlandı.

Plan referansı: `~/.claude/plans/melodic-bouncing-forest.md` (onaylı).

---

## Cluster A — Akustik feedback + kuyruk birikmesi ✅

**Belirti:** `User: How can I help?` / `User: I've turned the volume down a bit.`
gibi Jarvis'in kendi cümleleri transkript olarak geri geliyor; kuyruk hiç
boşalmıyor, `/status` 27 s sonra işlendi.

**Kök neden:**
- A1 — `ears/listener.py:_vad_record` mute kontrolü SADECE `not triggered` iken;
  kayıt bir kez `triggered=True` olunca Jarvis'in konuşmasını sonuna kadar
  kaydediyor.
- A2 — `core/app.py:run_jarvis` her cümleyi ayrı `speak()`'liyor; cümleler arası
  + `MIC_MUTE_COOLDOWN_S` boyunca `speaking_event` CLEAR.
- A3 — `core/input_hub.py` `queue.Queue()` sınırsız/FIFO, "konuşurken yakalanan
  girdiyi at" politikası yok.
- A4 — `_transcribe` (mic thread) ↔ `speak()` (ana thread) eşzamanlı → GPU
  çekişmesi, `Transkripsiyon gecikmesi: 3.65s`.

**Yapıldı:**
- [x] A1: `_vad_record` — `mute_event` set ise `triggered` olsa bile `return None`
  ("yankı sayılıp kayit iptal"). `mute_event` docstring'i güncellendi.
- [x] A2: `speak(..., manage_mute=False)` param + `tts.release_mic_mute()` helper;
  `run_jarvis` tur döngüsünü `speaking_event.set()` / `finally: release_mic_mute
  + discard_pending_voice` ile sardı; `_prompt_for_approval` ve
  `_run_delegate_code` notice `speak()` çağrıları `manage_mute=False`.
- [x] A3: `InputHub.discard_pending_voice() -> int`; `run_jarvis` `finally`'de.
- [x] A4: A1+A2 dolaylı çözdü (mute'ta `_vad_record` hızla `None`, transkripsiyon
  hiç çağrılmaz). `/test` yolunda approval anonsu mic-mute'suz — dev-tool, kabul
  (bkz. backlog).

**Test:** `test_input_hub.py` +2 (discard), yeni `test_listener.py` +2
(mute-abort + invariant). Full suite 225 pass / 1 skip.
Manuel (bekliyor): sessiz oda / kulaklık kontrol / kendi sesi izolasyonu.

**Commit:** `59faa9e`

---

## Cluster B — Whisper boş/gürültü halüsinasyonu ✅

**Belirti:** `VAD filter removed 00:00.870 of audio` (klibin %100'ü) → yine de
`User: Hello, system online.` (birebir `initial_prompt`'tan).

**Kök neden:**
- B1 — `initial_prompt` near-silence'ta aynen geri kusuluyor.
- B2 — `webrtcvad` (aggr. 2) küçük blip'e tetikleniyor; `_transcribe` `info`/
  `no_speech_prob`/`avg_logprob`/süre gate'i yapmıyor.
- B3 — `Detected language 'en' probability 0.52` sabit null-detection imzası.

**Yapıldı:**
- [x] B1: `condition_on_previous_text=False`; `_INITIAL_PROMPT` sabitleştirildi,
  içeriği korundu.
- [x] B2: `no_speech_threshold=0.6`, `log_prob_threshold=-1.0`,
  `compression_ratio_threshold=2.4`; segment-bazı `no_speech_prob>0.6 &
  avg_logprob<-0.5` → at; `_is_probable_hallucination()` tam-metin blocklist'i
  (`_HALLUCINATION_PHRASES`).
- [x] B3: `_vad_record` `voiced_frames` sayacı + `MIN_SPEECH_FRAMES=3`;
  `_transcribe` `<MIN_TRANSCRIBE_SECONDS (0.35s)` → model çağrılmadan `None`;
  `VAD_AGGRESSIVENESS 2→3`.

**Test:** `test_listener.py` +6 (transcribe gate + blip + invariant). Full suite
231 pass / 1 skip. Manuel (bekliyor): sessiz oda (sıfır transkripsiyon çağrısı).

**Not:** `VAD_AGGRESSIVENESS=3` + `MIN_SPEECH_FRAMES=3` ayarlanabilir — gerçek
kullanımda çok kısa gerçek komutlar ("dur") düşerse gevşetilir.

**Commit:** `00074f2`

---

## Cluster C — Router kalitesi (qwen2.5:3b) ✅

**Belirti:** "Jarvis şarkıyı devam ettir" → `run_command: taskmgr /restart`;
"sesi kıs" → `media_volume_up`; her karar `güven: 0.90` (hardcoded).

**Kök neden:**
- C1 — `_ROUTER_SYSTEM_PROMPT` ~50 satır yoğun İngilizce; 3B model tutmuyor.
- C2 — `run_command` için "transkript gerçekten komut dikte ediyor mu" kontrolü yok.
- C3 — `confidence=0.9` hardcoded.

**Yapıldı (karar: prompt + confidence + guard; model DEĞİŞMEZ):**
- [x] C1: `_ROUTER_SYSTEM_PROMPT` ~30 satıra sadeleştirildi, 6 kural + 16
  few-shot (TR diakritikli media formları dahil). F-hafif'in delegate_complex
  few-shot'ları da buraya kondu.
- [x] C2: `_command_appears_in_transcript(cmd, text)` — komutun ilk token'ı
  transkriptte kelime olarak yoksa `run_command` reddedilir → chat.
- [x] C3: `_selection_confidence(tool, args)` — required'lar dolu → 0.8, eksik →
  0.6; `no_tool_needed` zaten 0.6. `console.py` değişmedi (mevcut `güven: %.2f`
  formatı artık gerçekten değişiyor).

**Test:** `test_dispatcher_router.py` +3 (guard + confidence). Yeni
`test_router_accuracy.py` — opt-in batarya (`JARVIS_ROUTER_BATTERY=1`), 22 vaka,
gerçek Ollama: 20 pass, 2 xfail (bilinen 3B sınırı: "hatırla"→note, çok-adım
flaky). Full default suite 234 pass / 23 skip.

**Commit:** `ab07056`

---

## Cluster D — TTS içeriği + ses seviyesi kademesi ✅

**Belirti:** `Jarvis: Command output: ... Directory of C:\...` → 27.68 s TTS.
Ses aç/kıs tek keypress (~%2), belirsiz.

**Kök neden:**
- D1 — `RunCommandTool` ham çıktıyı döndürüyor; 200 char'a kırpılsa bile içerik
  (tarih/`<DIR>`) XTTS için patolojik.
- D3 — `media_tool.py` volume tek `_send_vk`, magnitude yok.

**Yapıldı:**
- [x] D1: `RunCommandTool` → kısa sesli onay (`_OK_MESSAGES` / `_FAILED_TEMPLATES`
  artık çıktı içermiyor); tam çıktı `print_system("Komut çıktısı:\n…")` ile
  konsola/HUD'a, `OUTPUT_CHAR_LIMIT 200→2000`. `ReadNotesTool` sesli kısım
  `READ_NOTES_SPOKEN_CHAR_LIMIT=400` + "… (devamı Obsidian'da)"; tam metin
  konsola.
- [x] D3: `_send_vk(vk, times=1)` döngü; `VOLUME_STEP_PRESSES=4` varsayılan;
  `_resolve_volume_steps(params)` — `amount` "biraz"→2, "çok"→8, sayı→clamp
  1..15, yok→4. `media_volume_up/down` şemasına `amount` (opsiyonel) eklendi.

**Kabul edilen sınır:** `delegate_complex` zincirinde `run_command` sonucu artık
kısa onay → tool_agent bir sonraki adım için ham çıktıyı göremez. Nadir + HIGH
onaylı yol; 6.10 ile birlikte tekrar bakılır (backlog).

**Test:** `test_media_tool.py` +5 (N-press + amount), `test_tools.py` +1
(run_command konsola), +1 (read_notes cap). Full suite 238 pass / 23 skip.

**Commit:** `6d0a30a`

---

## Cluster E — Dil tespiti ✅

**Belirti:** "Please specify your request" → `dil=es`; kısa kliplerde `fr`/`de`.

**Kök neden:** `core/language.py:detect_language` langdetect'e 2–3 kelimede
güveniyor.

**Yapıldı:**
- [x] E1: `detect_language` sonucu artık HER ZAMAN tr/en. `<4 kelime` → langdetect
  atlanır, `_TR_CHARS` sezgisi (çğıöşü…). `>=4 kelime` → langdetect ama tr/en
  dışı sonuç aynı sezgiyle tr/en'e indirilir. `_tr_or_en()` helper.

**Kabul edilen sınır:** ASCII'ye düşmüş kısa Türkçe ("sesi ac") → en; gerçek
Almanca → en fonetiği (Jarvis kapsamı dışı).

**Test:** yeni `test_language.py` +7. Full suite 245 pass / 23 skip.

**Commit:** `a308790`

---

## Cluster F — Çoklu adım planlama (hafif) ✅

**Belirti:** delegate_complex logda hiç tetiklenmedi; "çoklu plan yapamıyor".

**Kök neden:** router 3B nadiren `delegate_complex` seçiyor; `_MAX_DELEGATE_STEPS=3`;
`_TOOL_AGENT_SYSTEM_PROMPT` zayıf.

**Yapıldı (hafif — 6.10 kapsamlı versiyonu ayrı):**
- [x] `_MAX_DELEGATE_STEPS 3→5` (`app.py`).
- [x] `_TOOL_AGENT_SYSTEM_PROMPT` netleştirildi (tek araç/adım, "başarılı çağrıyı
  tekrarlama", önceki sonucu argümana taşı, bitince tek cümle özet).
- [x] `_ROUTER_SYSTEM_PROMPT` delegate_complex few-shot Cluster C'de artırıldı.

**Test:** yeni `test_delegate_complex.py` +3 (tamamlama + step cap + sabit).
Full suite 248 pass / 23 skip.

**Commit:** `8eabec7`

---

## Cluster G — Kozmetik ✅

**Belirti:** `/status` 2026-08-28 bayat pending onayları gösteriyor; clap
near-miss log spam.

**Yapıldı:**
- [x] G1: `pending_tasks.set_status(id, status)` (fail-soft). `cli_commands.py`
  `/deny <id>` / `/deny all` → `status='denied'` (migrations/002 CHECK'inde
  zaten var; `dismissed` yok). `_cmd_status` pending satırına `_relative_age()`
  ("3 gün önce"). Otomatik `/approve`+çalıştırma roadmap 6.6'da ertelendi —
  bu sadece listeden düşürme.
- [x] G2: `listener.py` clap near-miss logları (`info→debug`) — müzik/konuşma
  açıkken her chunk'ta basılıp logu boğuyordu. Başarılı "Cift alkis algilandi"
  `info` kaldı.

**Test:** `test_pending_tasks.py` +3, `test_cli_commands.py` +3. Full suite
254 pass / 23 skip.

**Commit:** `b598f0d`

---

## Durum: A–G kod + test ✅ (2026-08-29) — manuel doğrulama bekliyor

Tüm kümeler commit'lendi (`9c8ca5a`→`b598f0d`); `python -m pytest tests/`
**254 pass / 23 skip**. `python main.py` ile gerçek mikrofon doğrulaması:

- [ ] **Feedback**: hoparlör açık, "kendinden bahset" → uzun yanıt → sus. Log'da
  `User: <Jarvis cümlesi>` = 0; tur sonunda "yankı/feedback ses olayı kuyruktan
  atıldı" logu.
- [ ] **Sessiz oda**: 5 dk sessizlik → sıfır `User:`, sıfır `transkribe ediliyor`.
- [ ] **Kulaklık kontrol grubu**: aynı senaryo kulaklıkla düzgün.
- [ ] **Gecikme**: 5 komut arka arkaya; kuyruk birikmesi yok.
- [ ] **Router**: "sesi kıs"→volume_down, "şarkıyı devam ettir"→play_pause,
  run_command uydurması yok (`JARVIS_ROUTER_BATTERY=1 pytest
  tests/test_router_accuracy.py` ile de).
- [ ] **TTS**: `/test run_command command=dir` → kısa sesli onay, `dir` çıktısı
  sadece konsolda.
- [ ] **Ses kademesi**: "sesi çok aç" belirgin fark yaratıyor mu.
- [ ] **Dil**: 10 net TR + 10 net EN → `dil=es/fr/de` yok.
- [ ] **VRAM**: `nvidia-smi -l 1` — tepe 12 GB altında mı (backlog).
- [ ] **`/deny`**: `/status` bayat `#1/#2` → `/deny all` → listeden düşüyor.

---

## Yeni bulgular (backlog)

_(Yeni bug/optimizasyon sorunları buraya tarihli eklenir. Format: `- [YYYY-MM-DD]
belirti — kök neden (dosya:satır) — durum`)_

- 2026-08-29 — VRAM tepe kullanımı ölçülmedi: hermes3:8b + qwen2.5:3b + Whisper +
  XTTS aynı anda 12 GB'ı aşabilir (ROADMAP Faz 6.2 notu). `nvidia-smi -l 1` ile
  canlı ölçülmeli. — ⬜
- 2026-08-29 — `pending` (approval sırasında biriken voice olayları) `run_jarvis`
  içinde `discard_pending_voice`'tan etkilenmiyor; feedback approval penceresine
  sızarsa işlenir. Düşük olasılık, A3 sonrası tekrar değerlendir. — ⬜
- 2026-08-29 — `/test <araç>` (dev komutu) HIGH/MEDIUM bir araç için approval
  anonsunu (`speak` `manage_mute=False`) mic-mute'suz çalıştırır; anons hoparlörde
  yankılanırsa kuyruğa `voice` olarak girer, approval sonrası bir spurious tur
  işlenir. Zararsız (sesli onay devre dışı), dev-tool. İstenirse `_cmd_test`'e
  set/`release_mic_mute` sarması eklenir. — ⬜
- 2026-08-29 — Mutlak ses kontrolü ("sesi %50 yap") `pycaw`/Core Audio gerektirir;
  CLAUDE.md "gereksiz bağımlılık" ilkesi — şimdilik kapsam dışı. — ⬜
- 2026-08-29 (D1 sonrası) — `delegate_complex` zincirinde `run_command` çıktısı
  artık tool_agent'a kısa onay olarak dönüyor (ham çıktı yok); çok-adımlı
  "komutu çalıştır sonra sonucuna göre..." senaryosu zayıfladı. 6.10 kapsamlı
  orkestrasyonla birlikte `spoken_form()` hook'u değerlendirilir. — ⬜

### 2026-08-29 2. canlı test (A–G sonrası) — sonuçlar iyi, kalan ufak maddeler
- ✅ Feedback döngüsü gitti (logda sıfır self-echo turu), kuyruk gecikmesi yok.
- ✅ `run_command` guard gerçek bir uydurmayı yakaladı ("Jarvis sitesi kıs" →
  `curl … | grep …` reddedildi → chat).
- ✅ `amount` çıkarımı çalışıyor ("biraz aç"→biraz, "baya bir kıs"→cok).
- ⬜ `media_volume_down`/`up` yanıt metni sabit "biraz" diyor — `amount: cok`
  iken "epeyce" demeli (`media_tool.py:_VOLUME_*_MESSAGES` `amount`'a göre).
- ⬜ TTS ilk-chunk gecikmesi ara sıra 3–4 s (`Ilk ses chunk'i hazir: 4.14s`) —
  muhtemelen `sentence_transformers` embed'i (Faz 6.5 `remember`) ile GPU
  çekişmesi; embed'i tur sonrası ayrı bir düşük-öncelikli thread'e almak.
- ⬜ STT hataları ("Sesli kız", "Stradik şarkıya geç", "Jarvis sitesi kıs") —
  whisper `turbo` TR kısa cümle doğruluğu; ayrı iş (custom prompt / dictionary
  boost / `hotwords`).
- 2026-08-29 (Faz 6.7 sonrası) — `adapters/agent_factory.py:ClaudeCodeAdapter`
  (`delegate_code` yolu) `claude -p`'yi başlatırken API-key scrub yapmıyor;
  `spawn_detached`'in `_API_KEY_ENV_VARS` deseni oraya da uygulanmalı
  (tutarlılık — "Jarvis'in başlattığı claude ASLA API key kullanmaz"). — ⬜

---

## Kapatılanlar

_(Tamamlanan maddeler commit ref'iyle buraya taşınır.)_
