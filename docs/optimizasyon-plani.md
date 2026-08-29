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

**Commit:** `<B>`

---

## Cluster C — Router kalitesi (qwen2.5:3b) ⬜

**Belirti:** "Jarvis şarkıyı devam ettir" → `run_command: taskmgr /restart`;
"sesi kıs" → `media_volume_up`; her karar `güven: 0.90` (hardcoded).

**Kök neden:**
- C1 — `_ROUTER_SYSTEM_PROMPT` ~50 satır yoğun İngilizce; 3B model tutmuyor.
- C2 — `run_command` için "transkript gerçekten komut dikte ediyor mu" kontrolü
  yok (`~dispatcher.py:328`).
- C3 — `confidence=0.9` hardcoded (`~dispatcher.py:350`).

**Yapılacaklar (karar: prompt + confidence + guard; model DEĞİŞMEZ):**
- [ ] C1: prompt ~25 satıra sadeleştir + 6–8 few-shot.
- [ ] C2: `_command_appears_in_transcript(cmd, text)` guard'ı.
- [ ] C3: gerçek confidence (0.8/0.6/0.3 senaryoya göre); `print_router_decision`.

**Test:** `test_dispatcher_router.py` (guard + confidence); yeni
`test_router_accuracy.py` (`@pytest.mark.integration`, 25 canned komut).

**Commit:** —

---

## Cluster D — TTS içeriği + ses seviyesi kademesi ⬜

**Belirti:** `Jarvis: Command output: ... Directory of C:\...` → 27.68 s TTS.
Ses aç/kıs tek keypress (~%2), belirsiz.

**Kök neden:**
- D1 — `RunCommandTool` ham çıktıyı döndürüyor (`~terminal_tool.py:159`); 200
  char'a kırpılsa bile içerik (tarih/`<DIR>`) XTTS için patolojik.
- D3 — `media_tool.py` volume tek `_send_vk`, magnitude yok.

**Yapılacaklar:**
- [ ] D1: `RunCommandTool` → kısa sesli onay; tam çıktı `print_system` ile
  konsola. `ReadNotesTool` sesli kısım ~400 char + "devamı Obsidian'da".
- [ ] D3: `_send_vk(vk, times=1)`; `VOLUME_STEP_PRESSES=4`; opsiyonel `amount`
  ("biraz"/2, def/4, "çok"/8, int/clamp 1-15).

**Test:** `test_media_tool.py` (N-press + amount); `test_tools.py` (run_command
kısa sesli form).

**Commit:** —

---

## Cluster E — Dil tespiti ⬜

**Belirti:** "Please specify your request" → `dil=es`; kısa kliplerde `fr`/`de`.

**Kök neden:** `core/language.py:detect_language` langdetect'e 2–3 kelimede
güveniyor.

**Yapılacaklar:**
- [ ] E1: `<4 kelime` → TR-karakter sezgisi (tr/en); `>=4 kelime` → langdetect
  ama tr/en dışı sonucu aynı sezgiyle tr/en'e indir.

**Test:** yeni `test_language.py`.

**Commit:** —

---

## Cluster F — Çoklu adım planlama (hafif) ⬜

**Belirti:** delegate_complex logda hiç tetiklenmedi; "çoklu plan yapamıyor".

**Kök neden:** router 3B nadiren `delegate_complex` seçiyor; `_MAX_DELEGATE_STEPS=3`;
`_TOOL_AGENT_SYSTEM_PROMPT` zayıf.

**Yapılacaklar (hafif — 6.10 kapsamlı versiyonu ayrı):**
- [ ] `_MAX_DELEGATE_STEPS 3→5` (`~app.py:102`).
- [ ] `_TOOL_AGENT_SYSTEM_PROMPT` netleştir (tek araç/adım, tekrar etme, özet).
- [ ] `_ROUTER_SYSTEM_PROMPT` delegate_complex few-shot artır (C1 ile birlikte).

**Test:** `test_app_risk_gate.py` (4-adım senaryo, step cap).

**Commit:** —

---

## Cluster G — Kozmetik ⬜

**Belirti:** `/status` 2026-08-28 bayat pending onayları gösteriyor; clap
near-miss log spam.

**Yapılacaklar:**
- [ ] G1: `pending_tasks.set_status(id, status)` + `/dismiss <id>` / `/dismiss all`;
  `_cmd_status` pending satırına yaş.
- [ ] G2: `listener.py` near-miss logları `info→debug`.

**Test:** `test_pending_tasks.py` (set_status), `test_cli_commands.py` (/dismiss).

**Commit:** —

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

---

## Kapatılanlar

_(Tamamlanan maddeler commit ref'iyle buraya taşınır.)_
