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
- [ ] **Çift alkış** gibi genlik/RMS tabanlı ikinci bir tetikleyici — kullanıcı
      fikri, wake-word'e alternatif/ek olarak değerlendirilebilir.
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

## 3. Mouth — TTS ⬜

Henüz kod yok; `main.py` içindeki yorum ("TTS will be next") ve venv'de
zaten kurulu olan `edge-tts` paketi bir sonraki adımın bu olduğunu gösteriyor.

Alt adımlar:
- [ ] `edge-tts` ile temel bir `speak(text: str)` fonksiyonu yaz (async API'yi
      senkron `run_jarvis()` akışına nasıl bağlayacağına karar ver —
      `asyncio.run` veya pipeline'ı async'e çevirme).
- [ ] Ana döngüye bağla: `run_jarvis()` içinde `print(jarvis_response)`
      yerine/yanında `speak(jarvis_response)` çağrısı.
- [ ] Gecikme ölçümü: Ears→Brain→Mouth toplam gecikmeyi ölç, darboğazı
      belirle (muhtemelen Brain'in ilk token'a kadarki süresi).
- [ ] Kesinti/iptal: kullanıcı konuşurken Jarvis konuşuyorsa ne olacak
      (barge-in) — MVP'de basitçe engellensin, ileride ele alınsın.

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
