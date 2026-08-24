# Jarvis — Detaylı Yol Haritası (alt adımlarla)

Bu dosya `CLAUDE.md`'deki kısa MVP listesinin genişletilmiş hâlidir. Her ana
adımın altında somut alt adımlar var. Durum etiketleri: ✅ tamam,
🟡 kısmen/MVP var-olgunlaştırılacak, ⬜ başlanmadı.

## 1. Ears — Ses/Girdi Pipeline ✅ (wake-word + latency profiling dahil, tamamlandı)

Mevcut: `audio_handler.py:listen_loop()` bir **state machine**: IDLE
(openWakeWord `hey_jarvis` ile "Hey Jarvis" dinlenir, transkripsiyon yok) ↔
ACTIVE (wake-word sonrası `webrtcvad` ile VAD-tabanlı dinamik kayıt, sabit
blok yok, disk'e yazmadan ndarray) → faster-whisper (`turbo` =
large-v3-turbo, `multilingual=True` + TR/EN `initial_prompt` ile serbest dil
algılama, `vad_filter=True`, CUDA/float16 + otomatik CPU/int8 fallback) →
metin. Wake-word ve transkripsiyon gecikmeleri loglanıyor. `main.py`
`listen_loop()`'u değişmeden tüketiyor.

Alt adımlar:
- [x] Sabit 5 sn blok yerine VAD/sessizlik-tabanlı kayıt — `webrtcvad-wheels`
      ile 30ms frame'ler, ~700ms sessizlik sonrası otomatik durma, 20sn üst
      sınır.
- [x] Cümle sınırı bölünmesi sorunu — dinamik VAD kaydı sabit pencereyi
      ortadan kaldırdığı için ayrıca çözülmedi (adım 1'e "subsume" oldu);
      ek olarak `vad_filter=True` ile transkripsiyon öncesi temizlik açıldı.
- [x] Sürekli dinleme modu — `listen_loop()` generator'ı, `main.py`'de
      `for user_text in listen_loop():` ile tüketiliyor.
- [x] Wake-word / State Machine — `ListenState` (IDLE/ACTIVE); IDLE'da
      `openwakeword` `hey_jarvis` modeli (bundled ONNX, pip paketiyle
      birlikte gelir — internet sadece `download_models()`'ın ilk
      çalıştırmada ağırlıkları çekmesi için gerekir, sonrasında idempotent/
      offline) her ~80ms'lik chunk'ı skorluyor; skor `WAKEWORD_THRESHOLD`
      (0.5) üstüne çıkınca ACTIVE'e geçilip mevcut `_vad_record` aynen
      kullanılıyor. IDLE ve ACTIVE **tek bir kalıcı `sd.InputStream`'i**
      paylaşıyor (state geçişinde mikrofon aç/kapa gecikmesi/ses kaybı
      olmasın diye).
- [x] Latency profiling — wake-word: tetiklenene kadarki toplam süre +
      ortalama chunk-başı inference gecikmesi (bu makinede ölçüldü: **~1.6ms
      /80ms chunk**, gerçek zamanlı bütçenin çok altında); transkripsiyon:
      `_transcribe()` başı-sonu süresi. İkisi de `logger.info` ile loglanıyor.
      `pipeline-debugger` commit öncesi 2 gerçek bulgu verdi, ikisi de
      düzeltildi: (a) wake-word'ü tetikleyen 80ms'lik chunk hiçbir yere
      kaydedilmeden atılıyordu — "Hey Jarvis" sonrası duraksamasız gelen
      komutun ilk hecesi kaybolabiliyordu; artık `_wait_for_wakeword` bu
      chunk'ı döndürüp `_vad_record`'un pre-roll'üne ekleniyor. (b)
      `stream.read()`'in `overflowed` bayrağı hiç loglanmıyordu (buffer
      taşması sessizce ses kaybına yol açabilirdi); artık `logger.warning`
      ile bildiriliyor.
- [x] Hata yönetimi — mikrofon açılamazsa (`PortAudioError`) veya konuşma
      algılanmazsa (timeout) `None`/log, çökme yok; `print` yerine
      `logging` kullanılıyor. `model.transcribe()` çağrısı da try/except ile
      sarılı — tek kötü turn `listen_loop()`'un sonsuz döngüsünü çökertmiyor
      (bu ikinci koruma `pipeline-debugger` incelemesinde bulunan bir açıktı,
      düzeltildi).
- [x] CPU fallback — `_load_model_with_fallback()` CUDA'da **sessiz bir
      warm-up transkripsiyonu** yapıp gerçek bir inference tetikliyor (salt
      `WhisperModel(...)` constructor'ı CUDA hatasını yakalamıyor —
      ctranslate2 nesneyi kurar ama hata ilk gerçek çağrıda patlıyor), hata
      olursa CPU/int8'e düşüyor. **Bu makinede fiilen tetiklendi ve
      çözüldü**: `cublas64_12.dll` eksikti; `audio_handler.py` başına
      eklenen Windows DLL-fix (venv'deki `nvidia-cublas-cu12`/
      `nvidia-cudnn-cu12` pip paketlerinin bin/ dizinlerini
      `os.add_dll_directory` ile PATH'e tanıtıyor) sonrası CUDA doğrulandı
      çalışıyor — "faster-whisper 'cuda' cihazinda yuklendi" logu görüldü.
      RTX 4070 hızlanması artık aktif.
- [x] Türkçe doğruluk — model `base` yerine `turbo` (large-v3-turbo) yapıldı.
      İlk denemede `language="tr"` sabitlenmişti; kullanıcı Türkçe/İngilizce
      karışık kullanım isteyince (ör. "Merhaba Jarvis, execute command")
      `language=` kaldırılıp `multilingual=True` (her segment için ayrı dil
      algılama, tek seferlik değil) + iki dilde örnek cümleler içeren
      `initial_prompt` ile serbest bırakıldı.

Gelecek/opsiyonel (kapsam dışı bırakıldı):
- [x] **Çift alkış** — `_wait_for_wakeword` içine RMS-tabanlı, 0.2-0.8sn
      pencereli çift-alkış tespiti eklendi (aynı chunk döngüsü, ekstra
      thread yok); wake-word ile aynı `return chunk` sözleşmesini paylaştığı
      için `listen_loop()` değişmedi. Kullanıcı gerçek mikrofonla doğruladı
      ("gayet iyi çalışıyor"). `CLAP_THRESHOLD`/`CLAP_MIN_GAP_MS`/
      `CLAP_MAX_GAP_MS` `audio_handler.py`'de ayarlanabilir sabitler.
- [ ] `hey_jarvis` modelinin Türkçe aksanla güvenilirliği düşük çıkarsa:
      `WAKEWORD_THRESHOLD` ayarı veya openWakeWord'ün custom-model eğitim
      akışı (ayrı, daha büyük bir görev).

**İnsan doğrulaması gerekiyor (headless ortamda yapılamadı):**
- Kullanıcı `python main.py` ile gerçek Türkçe/İngilizce konuşarak Ears→Brain
  zincirini (wake-word öncesi) doğruladı — bu kısım çalışıyor.
- **Henüz doğrulanmadı:** wake-word state machine'i gerçek "Hey Jarvis"
  sesiyle (bkz. `.claude/skills/verify-wakeword-pipeline`) — Türkçe aksanla
  tetiklenme güvenilirliği, wake-word söylemeden konuşmanın gerçekten
  Brain'e gitmediği, IDLE↔ACTIVE geçişlerinin akıcılığı.
- Pre-roll buffer (tetik öncesi ~90ms, ilk hecenin kırpılmaması için) gerçek
  mikrofonla, özellikle yumuşak başlayan cümlelerle (örn. "Şey, merhaba")
  doğrulanmalı.

## 2. Brain — LLM Katmanı 🟡

Mevcut: `main.py` — `ollama.chat` ile `llama3.1:8b`'ye tek turluk (system +
user) mesaj gönderiliyor, yanıt tek seferde dönüyor.

- [x] Bug fix: `MODEL_NAME` etiketsiz `"llama3.1"` idi, Ollama'da 404
      hatası veriyordu (Ollama tam tag bekliyor) — `"llama3.1:8b"` olarak
      düzeltildi.

Alt adımlar:
- [ ] Konuşma geçmişi/context yönetimi: her çağrıda `messages` listesi
      sıfırdan kuruluyor, önceki tur hatırlanmıyor — bir `conversation`
      state'i ekle (son N mesaj veya özet).
- [ ] Streaming yanıt: `ollama.chat(..., stream=True)` ile token bazlı
      yanıt — TTS'e (Mouth) cümle cümle beslemek için gerekecek.
- [ ] Model/parametre fallback: `llama3.1` yüklenemezse/Ollama kapalıysa
      davranış (şu an `except Exception` genel bir hata metni döndürüyor,
      kullanıcıya "Ollama çalışmıyor" gibi daha net bir sinyal ver).
- [ ] `SYSTEM_PROMPT`'u sabit string yerine ayrı bir yapılandırma/dosyaya
      taşımayı değerlendir (persona değişikliklerini kolaylaştırmak için).

## 3. Mouth — TTS ✅ (MVP tamam — XTTS-v2 voice cloning)

`edge-tts` (bulut) yerine kullanıcı tercihiyle Coqui **XTTS-v2** zero-shot
voice cloning'e geçildi — tüm pipeline'ın yerelde/offline çalışması
ilkesiyle tutarlı. `tts_handler.py`: proje kökündeki `jarvis_reference.wav`
referans alınarak model + konuşmacı embedding'leri (`gpt_cond_latent`,
`speaker_embedding`) import zamanında **bir kez** hesaplanıyor;
`speak(text, language=None)` her çağrıda sadece `model.inference_stream()`
çalıştırıp üretilen chunk'ları disk'e yazmadan doğrudan
`sounddevice.OutputStream`'e akıtıyor (gerçek streaming oynatma).

Alt adımlar:
- [x] `speak(text: str)` fonksiyonu — `main.py`'nin senkron `run_jarvis()`
      akışına doğrudan (asyncio olmadan) bağlandı; XTTS'in kendi streaming
      API'si zaten düşük gecikmeli olduğu için async'e geçmenin şu an somut
      bir faydası yok (bkz. `CLAUDE.md` Kod Stili notu).
- [x] Ana döngüye bağlandı: `main.py:run_jarvis()` içinde `print(jarvis_response)`
      sonrası `speak(jarvis_response)`.
- [x] Gecikme ölçümü: `tts_handler.py` model yükleme süresini, ilk-chunk
      gecikmesini ve toplam sentez+oynatma süresini `logger.info` ile
      logluyor (Ears'in latency profiling desenine paralel).
- [ ] Kesinti/iptal: kullanıcı konuşurken Jarvis konuşuyorsa ne olacak
      (barge-in) — MVP'de basitçe engellensin (mevcut durum zaten bu),
      ileride ele alınsın.
- [ ] Dil tespiti (`_detect_language`, `langdetect` ile) şu an pratikte hep
      "en" dönüyor çünkü `main.py`'deki `SYSTEM_PROMPT` yanıtları
      İngilizce'ye sabitliyor — Brain çok dilli olduğunda gerçek TR/EN
      okuma davranışı doğrulanmalı.
- [x] Kurulum riskleri gerçekleşti ve çözüldü: `coqui-tts==0.27.5`'in
      taban paketi `transformers>=4.57`'i pinsiz kabul ettiği için ilk
      kurulumda `transformers==5.15.1` çekildi — bu, XTTS'in tortoise/gpt
      katmanlarının kullandığı `transformers.pytorch_utils.isin_mps_friendly`
      fonksiyonunu kaldırmış (transformers 5.x breaking change), import'u
      kırdı. `transformers==4.57.6`'ya (son 4.x sürümü) pinlenerek çözüldü;
      bu da `huggingface_hub`'ı `1.28.0`'dan `0.36.2`'ye düşürdü (sorun
      çıkarmadı). Ayrıca `tokenizers` `0.23.1`den `0.22.2`'ye düştü —
      `pip check` + `faster-whisper`/`ollama` import testleri temiz çıktı.
- [x] **Daha büyük bir bulgu:** `torch` 2.9+'ta `torchaudio.load()`'ın
      varsayılan backend'i `torchcodec`'e taşınmış; `torchcodec` sistemde
      ayrıca kurulu bir paylaşımlı FFmpeg kütüphanesi arıyor (bu makinede
      yok). XTTS referans `.wav`'i okurken tam olarak bu path'e düşüyordu
      (`get_conditioning_latents` → `load_audio` → `torchaudio.load`),
      `RuntimeError: Could not load libtorchcodec` ile patlıyordu — ayrıca
      bu hata `python script.py 2>&1 | tail -N` şeklinde çalıştırılan
      komutlarda gerçek exit code'u maskeliyordu (pipe'ın son elemanı
      `tail` başarıyla bittiği için `0` dönüyordu; gerçek hata `$?`'yi
      doğrudan script'ten almadan görünmüyordu). Çözüm: sistem geneline
      FFmpeg kurmak yerine `tts_handler.py` içinde `torchaudio.load`'u
      `soundfile` (zaten kurulu, libsndfile tabanlı, FFmpeg gerektirmez)
      ile aynı `(tensor, sample_rate)` sözleşmesini taklit eden bir
      fonksiyonla **monkeypatch**'ledik — sadece referans ses yükleme
      path'i için, `inference_stream()`'in kendisi hiç dosya okumuyor.
- [x] VRAM: gerçek `speak()` çağrılarında sorun çıkmadı (model yükleme
      ~9sn, ilk chunk ~0.3-0.5sn, toplam sentez ~4sn/cümle, RTX 4070/12GB
      üzerinde CUDA'da). Whisper + Ollama + XTTS'in **aynı anda** birlikte
      kullanıldığı gerçek bir uçtan uca (`python main.py`) turu henüz
      insan tarafından doğrulanmadı.

## 4. Modüler Komut Yöneticisi ⬜

Alt adımlar:
- [ ] Intent şeması tasarımı: hangi alanlar zorunlu (intent adı, parametreler,
      güven skoru?).
- [ ] Rule-based ilk sürüm: basit anahtar kelime/regex eşleştirme ile birkaç
      örnek komut (ör. "saat kaç", "dosya listele").
- [ ] Modül yönlendirme arayüzü: intent → handler fonksiyon eşlemesi
      (`core/dispatcher.py` gibi).
- [ ] Hybrid/LLM-based'e geçiş: rule-based eşleşmezse Ollama'ya "bu komut
      hangi intent'e girer" diye sorduran bir katman.
- [ ] **Modülerleşme dönüm noktası burada**: `src/jarvis/{ears,brain,mouth,
      core,tools,agents}/` paket yapısına geçişi bu adımda yap (mevcut düz
      `main.py`/`audio_handler.py` bu paketlerin altına taşınır).

## 5. Sistem Entegrasyonları / Tool Use ⬜

Alt adımlar:
- [ ] Tool arayüz şeması: her tool'un adı, parametre şeması, dönüş tipi için
      ortak bir sözleşme (ör. `dict`/`dataclass` bazlı tanım).
- [ ] Dosya yönetimi tool'u: okuma/yazma/listeleme, erişim izin verilen
      dizinlerle sınırlı olmalı.
- [ ] Terminal komut çalıştırma tool'u — **başlanır başlanmaz
      `.claude/agents/security-reviewer` subagent'ı ile incelenmeli**
      (komut enjeksiyonu, ayrıcalık sınırı, LLM çıktısına aşırı güven riski).
- [ ] Sistem izleme tool'u: CPU/RAM/GPU/VRAM durumu (`nvidia-smi` sarmalayıcı).
- [ ] Harici API entegrasyonları: sır yönetimi `.env` üzerinden, asla koda
      gömülmeden.
- [ ] Opsiyonel: bu tool katmanını MCP standardına uygun bir sunucu olarak
      paketleme (bkz. `docs/claude-code-rehberi.md` §6) — hem Jarvis hem
      Claude Code aynı araçları kullanabilsin.

## 6. Otonom Ajan Döngüsü ⬜

Alt adımlar:
- [ ] Görev planlama/zincirleme: çok adımlı bir isteği alt görevlere bölme.
- [ ] Kısa vadeli hafıza (oturum içi context) vs uzun vadeli hafıza (disk/DB
      üzerinde kalıcı tercih/bağlam) ayrımı.
- [ ] Hata kurtarma: bir adım başarısız olursa yeniden dene / kullanıcıya
      sor / alternatif plana geç.
- [ ] Çok adımlı yürütme döngüsü: plan → araç çağrısı → sonucu değerlendir →
      devam et/bitir.
- [ ] Kullanıcı onay noktaları: riskli aksiyonlardan (dosya silme, dış API'ye
      veri gönderme) önce onay iste — `security-reviewer` bulgularıyla
      örtüşür.

---

## Notlar

- Bu dosya `CLAUDE.md`'nin 200 satır kısıtından muaf; detay burada birikir.
- Her adım tamamlandığında durum etiketini (✅/🟡/⬜) güncelle, böylece
  `CLAUDE.md`'deki kısa özetle senkron kalır.
- Yeni bir adıma başlarken önce **plan mode** ile keşif yap (bkz. rehber
  `docs/claude-code-rehberi.md` §7 "Günlük döngü").
