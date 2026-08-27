# Jarvis — Detaylı Yol Haritası (Faz 1–5)

Bu dosya `CLAUDE.md`'deki kısa MVP listesinin genişletilmiş hâlidir. Her fazın
altında somut alt adımlar var. Durum etiketleri: ✅ tamam,
🟡 kısmen/MVP var-olgunlaştırılacak, ⬜ başlanmadı.

Mimari tasarımın "nasıl/neden"i (design pattern'lar, multi-agent iletişim
şeması, VRAM optimizasyonu, güvenlik/guardrail tasarımı, genişletilmiş
klasör yapısı) `docs/ARCHITECTURE.md`'dedir — burası sadece eyleme
geçirilebilir görev listesidir.

## Faz 1 — Girdi/Çıktı Çekirdeği (MVP) ✅

### 1.1 Ears — Ses/Girdi Pipeline ✅ (wake-word + latency profiling dahil, tamamlandı)

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
- [x] **Çift alkış** — `_wait_for_wakeword` içine RMS-tabanlı, 0.15-0.8sn
      pencereli çift-alkış tespiti eklendi (aynı chunk döngüsü, ekstra
      thread yok); wake-word ile aynı `return chunk` sözleşmesini paylaştığı
      için `listen_loop()` değişmedi. `CLAP_THRESHOLD`/`CLAP_MIN_GAP_MS`/
      `CLAP_MAX_GAP_MS` `src/jarvis/ears/listener.py`'de ayarlanabilir
      sabitler.
      **Güvenilirlik bulgusu + düzeltme** (kullanıcı: "önce uzak/yakın fark
      etmeden çalışıyordu, şimdi birkaç deneme gerekiyor"): kök neden iki
      katmanlıydı. (1) Gürültü tabanı (`_clap_noise_floor`) sadece impulsif
      olmayan (`is_loud=False`) chunk'lardan güncelleniyordu, ama sürekli
      konuşma/ortam sesi de düşük crest-factor'lu olduğundan bu kategoriye
      giriyor ve tabanı sınırsızca yukarı sürüklüyordu — uzun bir konuşma
      sonrası dinamik eşik (`noise_floor*6`) gerçek alkışların RMS'ini
      geçebiliyordu; **`CLAP_NOISE_FLOOR_MAX=450`** tavanı eklendi ve
      EMA güncellemesi sadece gerçekten "rms-olarak yüksek olmayan"
      chunk'lardan yapılacak şekilde daraltıldı. (2) Uzaktan gelen alkışlar
      oda yankısı yüzünden daha düşük crest-factor'e sahip (gerçek testte
      4.1 ölçüldü) — `CLAP_MIN_CREST_FACTOR` 3.5'ten **3.0**'a düşürüldü.
      (3) Asıl baskın sebep: iki alkış arası zaman penceresi (`CLAP_MIN_GAP_MS`)
      200ms'ti, gerçek testte geçerli bir alkış çifti 159ms'de bu yüzden
      reddediliyordu (yeni eklenen near-miss loglarıyla görüldü) —
      **150ms**'e düşürüldü. Ayrıca her başarısız/near-miss algılama artık
      `logger.info` ile (rms/crest/gap değerleriyle) loglanıyor, ileride
      benzer bir ayar sorununda kör kalınmasın diye. Kullanıcı gerçek
      mikrofonla, önce yakından sonra özellikle uzaktan, düzeltme
      sonrası tekrar doğruladı ("çalışıyor").
- [ ] `hey_jarvis` modelinin Türkçe aksanla güvenilirliği düşük çıkarsa:
      `WAKEWORD_THRESHOLD` ayarı veya openWakeWord'ün custom-model eğitim
      akışı (ayrı, daha büyük bir görev).
- [ ] **Ek tetikleyici kelimeler** ("jarvis", "wake up", "uyan"): araştırıldı
      — openWakeWord'ün pip paketiyle gelen hazır (pretrained) modelleri
      sadece `alexa`/`hey_mycroft`/`hey_jarvis`/`hey_rhasspy`/`timer`/
      `weather`'dan ibaret; "jarvis" tek başına, "wake up" veya Türkçe
      "uyan" için hazır bir model yok. İki yol var: (a) her ifade için
      openWakeWord'ün sentetik-veri eğitim akışıyla ayrı bir ONNX modeli
      eğitmek (yukarıdaki maddeyle aynı — ayrı, daha büyük bir görev), ya
      da (b) zaten yüklü faster-whisper modelini ikinci bir tetikleyici
      yolu olarak kullanmak (IDLE'da enerji-tabanlı kısa bir konuşma
      tamponu biriktirip transkribe ederek kelime araması yapmak — eğitim
      gerektirmez ama openWakeWord'ün ~1.6ms/chunk hızına göre gecikmesi
      daha yüksek). Kullanıcı talebiyle bu oturumda kapsam dışı bırakıldı,
      ileride ayrı bir görev olarak ele alınacak.

**İnsan doğrulaması ✅ (gerçek "Hey Jarvis" ile, `python -u main.py` +
`.claude/skills/verify-wakeword-pipeline` kontrol listesiyle yapıldı):**
- Kullanıcı `python main.py` ile gerçek Türkçe/İngilizce konuşarak Ears→Brain
  zincirini (wake-word öncesi) doğruladı — bu kısım çalışıyor.
- [x] Wake-word state machine gerçek "Hey Jarvis" sesiyle iki ayrı IDLE→ACTIVE
      döngüsünde doğrulandı (skor=0.51 ve 0.67, eşiğin — 0.5 — üzerinde);
      wake-word söylemeden konuşulan cümleler (IDLE'da ~120sn boyunca)
      transkribe edilmedi/Brain'e gitmedi — yanlış-pozitif görülmedi.
- [x] Gecikme: wake-word ort. chunk gecikmesi her iki tetiklenmede de
      **2.3ms** (80ms bütçenin çok altında); transkripsiyon gecikmesi
      gerçek konuşmalarda 0.4-1.1s aralığında (CUDA/turbo).
- [x] Uçtan uca TR/EN doğrulandı: "Hello Jarvis. Nasılsın?" girdisi TR olarak
      algılanıp (p=0.94) Türkçe yanıt + Türkçe TTS sesiyle (gerçek
      `jarvis_reference_tr.wav` ile, artık fallback değil) okundu; "Hey
      Jarvis." girdisi EN yanıt + EN TTS sesiyle okundu — Faz 1.3'teki
      bilingual switch canlı ortamda da çalışıyor.
- [x] IDLE↔ACTIVE↔FOLLOWUP geçişleri restart gerekmeden akıcı çalıştı, iki
      ayrı wake-word döngüsü art arda başarıyla tetiklendi.
- [ ] Pre-roll buffer (tetik öncesi ~90ms, ilk hecenin kırpılmaması için)
      özellikle yumuşak başlayan cümlelerle (örn. "Şey, merhaba") ayrıca
      doğrulanmalı — bu turda dolaylı olarak sorun görülmedi ("Hello Jarvis"
      hiç kırpılmadan transkribe edildi) ama özel olarak test edilmedi.

**Bulgu düzeltildi ✅ — takip penceresi gürültüde gereğinden uzun sürüyordu:**
Kullanıcı gözlemi ("uyku moduna çok geç giriyor") loglarla doğrulanmıştı:
`webrtcvad`'in ortam gürültüsünü/nefes sesini "konuşma" sanıp tetiklenmesi
sonucu faster-whisper'ın kendi `vad_filter`'ı tarafından tamamen boşaltılan
(`VAD filter removed X of audio` = tüm klip) boş turlar, her biri tam bir
`FOLLOWUP_WINDOW_MS` (12sn) penceresini sıfırdan yeniden açıyordu — art
arda geldikçe IDLE'a dönüş kümülatif olarak çok gecikiyordu. **Düzeltme**
(`audio_handler.py:listen_loop()`): artık bir `followup_deadline` (mutlak
zaman) tutuluyor; deadline sadece **gerçek bir transkript** (boş olmayan
`text`) üretildiğinde tam `FOLLOWUP_WINDOW_MS`'e sıfırlanıyor, gürültü
kaynaklı boş turlar ise sadece kalan süreyi tüketiyor (yeni pencere
açmıyor) — `_vad_record`'a da her seferinde kalan süre `max_wait_ms`
olarak geçiliyor. Log satırı da buna göre "Takip penceresi acik (kalan
Xs)" şeklinde güncellendi. Gerçek mikrofonla iki senaryoda doğrulandı: (1)
gerçek bir soru-cevap sonrası art arda gürültü tetiklenmeleri "kalan 12s →
10s → 8s → 5s" şeklinde düzgün azalıp tam ~12sn'de IDLE'a döndü; (2) hiç
gerçek konuşma olmadan sadece gürültü/çift-alkış ile başlayan bir turda da
"kalan 12s → 9s → 6s → 3s → 1s" şeklinde azalıp yine ~12sn'de IDLE'a
döndü — sonsuza kadar ertelenme sorunu ortadan kalktı.

### 1.2 Brain — LLM Katmanı ✅

Mevcut: `main.py` — `ollama.chat`'e `llama3.1:8b` ile **streaming** ve
**geçmiş-bağlı (history)** bir istek gönderiliyor; `SYSTEM_PROMPT` artık
kod içine gömülü değil, `system_prompt.txt`'ten okunuyor.

- [x] Bug fix: `MODEL_NAME` etiketsiz `"llama3.1"` idi, Ollama'da 404
      hatası veriyordu (Ollama tam tag bekliyor) — `"llama3.1:8b"` olarak
      düzeltildi.
- [x] `SYSTEM_PROMPT` artık yanıtları İngilizce'ye sabitlemiyor —
      kullanıcının kullandığı dilde (TR/EN) yanıt verme kuralına çevrildi
      (1.3'teki çift-dilli TTS'i canlı ortamda kullanılır kıldı).

Alt adımlar:
- [x] **Konuşma geçmişi/context yönetimi**: `run_jarvis()` bir `history`
      listesini (`system` mesajıyla başlayan) döngü boyunca kalıcı tutuyor,
      her tur `think_and_respond_stream()`'e referans olarak geçiyor ve
      `user`/`assistant` mesajlarıyla büyüyor. `_trim_history()`,
      `MAX_HISTORY_MESSAGES = 12` (son 6 kullanıcı+6 asistan turu) sınırını
      aşan en eski mesajları atıyor (system mesajı hariç) — yerel 8B
      modelde her ek mesaj gecikme/işlem yükü kattığından. Doğrulama: "Benim
      adım Ömer, bunu hatırla" → "Benim adım ne?" takip sorusuna doğru
      "Ömer." yanıtı alındı (gerçek hafıza çalışıyor).
- [x] **Streaming yanıt + cümle-cümle TTS**: `think_and_respond_stream()`
      `ollama.chat(..., stream=True)` ile token akışını okuyup bir
      cümle-sonu regex'iyle (`(?<=[.!?])\s+`) tamamlanan her cümleyi
      `yield` ediyor; `run_jarvis()` bunları tek tek `speak()`'e besliyor —
      TTS ilk cümle hazır olur olmaz başlıyor, LLM geri kalanını üretirken
      paralel ilerliyor (gerçek mikrofonla uçtan uca doğrulandı).
- [x] **Model/bağlantı fallback**: `think_and_respond_stream()`'in hata
      bloğu üçe ayrıldı — `httpx.ConnectError`/`ConnectionError` (Ollama
      kapalı — not: `ollama` paketi bu dönüşümü sadece non-streaming yolda
      yapıyor, streaming'de ham `httpx.ConnectError` sızıyor, ikisi de
      yakalanıyor) → net TR/EN "Ollama'ya bağlanamıyorum" mesajı;
      `ollama.ResponseError` + `status_code == 404` (model çekilmemiş) →
      `ollama pull {MODEL_NAME}` talimatlı net mesaj; diğerleri → genel
      teşhis mesajı. Üçü de sahte `OLLAMA_HOST`/geçersiz model adıyla
      izole test edildi (gerçek Ollama süreci durdurulmadan), history'ye
      hatalı bir `assistant` mesajı eklenmediği doğrulandı.
- [x] `SYSTEM_PROMPT` artık `system_prompt.txt` dosyasında (proje kökü) —
      persona değişikliği için kod dokunulmuyor; dosya yoksa
      `tts_handler.py`'nin `REFERENCE_AUDIO_EN` deseniyle tutarlı şekilde
      açık `FileNotFoundError` ile patlıyor.

### 1.3 Mouth — TTS ✅ (MVP tamam — XTTS-v2 voice cloning + VRAM-optimize çift-dilli)

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
- [x] Dil tespiti (`_detect_language`, `langdetect` ile) artık gerçekten iki
      dil arasında değişiyor: `main.py`'deki `SYSTEM_PROMPT`'un "Always
      respond STRICTLY in English" kuralı kaldırılıp kullanıcının kullandığı
      dilde (TR/EN) yanıt verme kuralına çevrildi (bkz. 1.2). `.claude/skills
      /verify-brain-pipeline` ile Türkçe ve İngilizce örnek girdiler
      denendi: Türkçe girdiye Türkçe ("Günaydın! Bugün Londra'da güneşli ve
      serin bir hava var...") , İngilizce girdiye İngilizce yanıt doğrulandı.
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
- [x] **VRAM-optimize tek-motor çift-dilli (TR/EN) TTS**: `tts_handler.py`
      tek `Xtts` model instance'ı üzerinde, dile göre seçilen iki
      referans/embedding çifti tutuyor: `REFERENCE_AUDIO_EN =
      jarvis_reference.wav` (zorunlu, mevcut) + `REFERENCE_AUDIO_TR =
      jarvis_reference_tr.wav` (opsiyonel — proje kökünde henüz yok).
      `_compute_voice_profiles()` her ikisi için `get_conditioning_latents()`
      hesaplayıp `_voice_profiles = {"en": ..., "tr": ...}` sözlüğünde
      saklıyor; TR dosyası bulunamazsa `logger.warning` ile bildirilip
      `"tr"` anahtarı da EN embedding'ine düşürülüyor (özellik dosya
      eklenmeden de çalışır, gerçek bir TR dublaj örneği eklendiğinde kod
      değişikliği gerekmiyor). `speak()` artık `lang == "tr"` ise TR,
      değilse EN profilini `_produce_tts_chunks()`'a açık parametre olarak
      geçiriyor (önceki modül-global `_gpt_cond_latent`/`_speaker_embedding`
      bağımlılığı kaldırıldı). Manuel doğrulama: `speak(text,
      language="en")` ve `speak(text, language="tr")` ayrı ayrı çağrılıp
      ikisinin de hatasız sentezlendiği/çalındığı, TR fallback uyarısının
      bir kez loglandığı görüldü (VRAM etkisi: yükleme ~10.5s, iki dil
      birlikte, ek model yok — bkz. `docs/ARCHITECTURE.md` §5).

## Faz 2 — Agentic Orkestrasyon & Guardrail ✅

**Not:** `main.py`, `audio_handler.py`, `tts_handler.py` bu fazda
`src/jarvis/{ears,brain,mouth}/` altına taşındı (Faz 1'in yukarıdaki
anlatımı o zamanki, taşıma-öncesi duruma ait tarihsel bir kayıttır).

**Canlı döngüye bağlandı ✅**: `core/app.py:_handle_turn()` artık her
turu sırayla (1) girdi guardrail'i (`InputInjectionCheck` — red ise
Brain'e hiç gidilmez, iki dilli ret mesajı döner), (2) **sadece
kural-tabanlı** hızlı dispatch (`Dispatcher.match_rule()` — LLM'e hiç
gitmez, bilinen bir handler varsa Brain'e hiç gidilmeden direkt cevap
döner), (3) yoksa normal streaming sohbet + her cümle için çıktı
guardrail'i (`OutputSafetyCheck` — red ise o cümle sessizce atlanır)
sırasından geçiriyor. Gerçek mikrofonla doğrulandı: normal sohbet, "saat
kaç?" (Brain'e hiç gitmeden handler'dan cevap) ve izole bir injection
testi hepsi doğru çalıştı.

*Bilinçli tasarım kararı:* `classify()`'in LLM-fallback'i (hibrit,
Faz 3'te daha fazla intent olduğunda anlamlı) canlı döngüde
KULLANILMIYOR — her sıradan sohbet turunda ekstra bir "bu ne intent'i"
LLM çağrısı, gerçek cevap için ikinci bir çağrıyla birlikte gecikmeyi
ikiye katlardı. `match_rule()` bunun için eklendi (sadece `_RULES`,
LLM'e gitmez); `classify()` değişmeden, ayrı bir yol olarak duruyor.

### 2.1 Modüler Komut Yöneticisi ✅

Alt adımlar:
- [x] Intent şeması tasarımı: `core/dispatcher.py`'de Pydantic `Intent`
      (`name`, `confidence`, `parameters`, `source: "rule"|"llm"`).
- [x] Rule-based ilk sürüm: `_RULES` sözlüğünde "saat kaç"→`get_time`,
      "dosya listele"→`list_files` regex örnekleri.
- [x] Modül yönlendirme arayüzü: `core/handlers.py` — `HANDLERS: dict[str,
      Callable[[Intent], str]]`. Şimdilik sadece `get_time` gerçek bir
      handler'a sahip (dosya/sistem erişimi gerektirmiyor, iki dilde
      cevap veriyor — `datetime.now()`); `list_files` bilinçli olarak
      handler'sız bırakıldı, çünkü gerçek dosya listeleme ROADMAP'in
      kendi tanımına göre Faz 3.1'in erişim-kontrollü tool'u — handler'ı
      olmayan her intent otomatik olarak normal sohbete düşüyor.
- [x] Hybrid/LLM-based'e geçiş: `classify()` kural eşleşmezse
      `AgentFactory.create("orchestrator")` üzerinden Ollama'ya "bu metin
      hangi intent'e girer" diye sorup `source="llm"` ile dönüyor (canlı
      döngüde kullanılmıyor, bkz. yukarıdaki tasarım kararı — ama
      bağımsız test edilmiş durumda, Faz 3'te devreye alınabilir).
- [x] **Modülerleşme dönüm noktası**: `src/jarvis/{ears,brain,mouth,core,
      adapters,agents}/` paket yapısına geçiş tamamlandı — `audio_handler.py`
      → `src/jarvis/ears/listener.py`, `tts_handler.py` →
      `src/jarvis/mouth/tts.py`, `main.py`'nin LLM mantığı →
      `src/jarvis/brain/llm.py`, döngü → `src/jarvis/core/app.py`; kökte
      ince bir `main.py` giriş noktası kaldı. Statik import testiyle ve
      gerçek mikrofonla uçtan uca doğrulandı — davranış değişmedi.
      `.claude/skills/verify-*` komutları ve `CLAUDE.md`/`docs/ARCHITECTURE.md`
      dosya-yolu referansları da güncellendi.

### 2.2 Multi-Agent Orkestrasyon (Orkestratör → Hermes → Claude Code) ✅

`docs/ARCHITECTURE.md` §3–4'te tanımlanan Factory/Adapter tabanlı ajan
ağının ilk somut uygulaması:

- [x] `Agent` arayüzü (`respond()`, `supports_tools()`, `agents/base.py`)
      ve `AgentFactory` (`adapters/agent_factory.py`, `role→Agent`
      eşlemesi: `"orchestrator"|"tool_agent"|"deep_reasoning"`).
- [x] `LlamaOrchestratorAdapter` — `llama3.1:8b` ile senkron `respond()`
      (bağımsız, yeni bir implementasyon — `brain/llm.py`'nin streaming
      `think_and_respond_stream()`'ini sarmalamıyor, ikisi şimdilik ayrı
      duruyor).
- [x] Intent sınıflandırma kuralları: `core/dispatcher.py`'nin LLM-fallback
      yolu bu adapter'ı kullanıyor (bkz. 2.1).
- [x] **`HermesAgentAdapter`** — kullanıcı tercihiyle VRAM-notundaki
      "paylaşımlı model" önerisi yerine **gerçek, ayrı bir model**
      (`hermes3:8b`, `ollama pull` ile indirildi) kullanılıyor; Ollama'nın
      model swap/`keep_alive` mekanizmasıyla aynı anda VRAM'de iki 8B
      modelin birden tutulması gerekmiyor (bkz. `docs/ARCHITECTURE.md`
      §5 "sıralı yükleme" seçeneği). Gerçek `respond()` çağrısıyla test
      edildi. Tool-calling bağlanması hâlâ Faz 3'e ait.
- [x] `ClaudeCodeAdapter` — **stub**: `anthropic` SDK'sı kurulu değil,
      `ANTHROPIC_API_KEY` `.env`'de yok; `respond()` net bir
      `NotImplementedError` ile ne eksik olduğunu söylüyor. Gerçek
      bağlantı ayrı bir görev. **Revize plan (⬜)**: doğrudan Anthropic API
      yerine `terminal_tool` üzerinden `claude` CLI'ı tetikleyen bir
      "Alt Yüklenici" deseni — bkz. Faz 4.5 ve `docs/ARCHITECTURE.md` §9.4.

### 2.3 AI Guardrail Katmanı ✅

`docs/ARCHITECTURE.md` §6'daki Chain-of-Responsibility tasarımının ilk
sürümü, `core/guardrail/`:

- [x] `GuardrailCheck` (ABC) + `GuardrailChain` (`base.py`) — sırayla
      çalıştırır, ilk red'de durur.
- [x] Girdi tarafı: `InputInjectionCheck` (`input_checks.py`) — OWASP LLM01
      kalıpları (TR+EN: "ignore previous instructions", "önceki
      talimatları yok say" vb.), regex tabanlı.
- [x] Çıktı tarafı: `OutputSafetyCheck` (`output_checks.py`) — tehlikeli
      komut kalıpları (`rm -rf`, `format`, fork bomb, `DROP TABLE`,
      `shutdown` vb.).
- [x] Guardrail red/kabul kararlarının loglanması: `GuardrailChain.run()`
      her kararı (`check_name` + `reason`) `logging` ile basıyor.
- [x] OWASP LLM Top 10 eşlemesinin (bkz. `docs/ARCHITECTURE.md` §6) test
      senaryoları: `tests/test_guardrail.py` (yeni, `pytest` eklendi —
      `requirements.txt`), kod-seviyesinde test edilebilir iki satır için
      gerçek testler — LLM01 (Prompt Injection → `InputInjectionCheck`,
      TR+EN kalıp + masum cümle örnekleri) ve LLM02 (Insecure Output
      Handling → `OutputSafetyCheck`, tehlikeli komut + masum çıktı
      örnekleri), `python -m pytest tests/ -v` ile 4/4 geçiyor. Tablodaki
      diğer satırlar (LLM06/08/09) kod-seviyesinde bir check değil,
      tasarım/süreç ilkesiyle karşılanıyor — testte bir yorumla
      netleştirildi, uydurma test yazılmadı.

## Faz 3 Öncesi — Kritik Bug-Fix Yaması ✅

Faz 2 tamamlandıktan hemen sonra, kullanıcının gerçek kullanımda bildirdiği
3 hata düzeltildi (`src/jarvis/{core/app.py,ears/listener.py,mouth/tts.py,
core/dispatcher.py,core/handlers.py}` + yeni `core/language.py`):

- [x] **Graceful shutdown**: `core/app.py:run_jarvis()` artık bir
      `threading.Event` (`stop_event`) oluşturup `listen_loop()`'a ve
      `speak()`'e geçiriyor; her ikisi de kendi iç döngülerinde (ses
      frame'i / chunk bazında) bunu periyodik kontrol edip erken çıkıyor.
      Ctrl+C `run_jarvis()`'te `except KeyboardInterrupt` ile yakalanıp
      `stop_event.set()` çağırıyor. İzole testlerle doğrulandı:
      `listen_loop()` 0.02sn'de, `speak()` (ilk halinde 3sn süren bir
      gecikme bulunup düzeltildikten sonra) 0.31sn'de temiz çıkıyor —
      `speak()`'teki asıl gecikme `sd.OutputStream`'in normal `close()`'unun
      (PortAudio `Pa_StopStream`) yazılmış ses tamponunun çalınmasını
      BEKLEMESİYDİ; kapatma anında bunun yerine `out.abort()` (`Pa_
      AbortStream`, beklemeden anında durur) çağrılarak çözüldü. Gerçek
      terminalde `python -u main.py` + Ctrl+C ile uçtan uca doğrulandı
      ("terminalde denedim çalışıyor"). **Bilinen sınırlama**: hâlihazırda
      çalışan TEK bir bloklayıcı model çağrısını (bir faster-whisper
      transkripsiyonu, bir Ollama isteği, bir XTTS inference chunk'ı)
      yarıda kesemez — bunlar Python'un sinyal kontrol noktalarına dönene
      kadar beklenir; sadece bu çağrılar ARASINDAKI bekleme sürelerini
      anında kısaltır.
- [x] **Ses üst üste binmesi**: `mouth/tts.py`'de modül-seviyesi bir
      `_PLAYBACK_LOCK` eklendi — `speak()`'in tüm gövdesini sarıyor, iki
      çağrının sesleri kod-seviyesinde asla üst üste binemez (savunma
      amaçlı garanti; mevcut akışta zaten sıralı çağrılıyordu).
- [x] **Kural-tabanlı yanıtlarda dil kayması**: kök neden, `core/handlers.py`
      ve `core/app.py`'nin Brain'i (ve SYSTEM_PROMPT'un dil kuralını) hiç
      devreye sokmadan ürettiği şablonların TEK bir XTTS `lang` bayrağı
      için İKİ dili birleştirmesiydi (örn. "Şu an saat 01:49. It's 01:49
      now." → `lang=en` seçilip Türkçe kısım İngilizce fonetikle
      okunuyordu). Düzeltme: `core/dispatcher.py`'deki `_RULES` artık her
      intent için dile göre AYRI regex alternatifleri tutuyor
      (`list[tuple[str, re.Pattern]]`) — hangi alternatifin eşleştiği
      doğrudan doğru dili veriyor (`langdetect`'e hiç güvenmeden; ilk
      denemede `langdetect`'in kısa metinlerde — "saat kaç?" gibi —
      güvenilmez çıktığı gerçek testte görüldü). `core/handlers.py`'nin
      `_handle_get_time`'ı artık TEK dilde, doğru şablonla `(text, lang)`
      döner; `core/app.py`'nin girdi-guardrail ret mesajı da aynı desenle
      (yeni paylaşılan `core/language.py:detect_language()` ile, serbest
      metin için) tek dilde düzeltildi. Gerçek mikrofonla "saat kaç?" → TR,
      "what time is it?" → EN, ikisi de doğru dilde doğrulandı.
- [x] Ek: `ears/listener.py`'ye model yükleme öncesi daha açıklayıcı
      durum logları eklendi ("... yukleniyor (birkaç saniye sürebilir)...").

## Faz 3 — Sistem Entegrasyonları & Zero-Trust Güvenlik ⬜

### 3.1 Tool Use 🟡 (yerel araçlar + Spotify tamam, Takvim bekliyor)

**Tetikleme yöntemi (bilinçli karar, SONRADAN DEĞİŞTİ — bkz. 3.3):** araçlar
`core/dispatcher.py`'deki **rule-based regex** ile tetikleniyordu — gerçek
function-calling (serbest doğal dille "Jarvis bir hatırlatma bırak..." demek)
ayrı ve büyük bir sonraki adım olarak bırakılmıştı. Bugün regex dışında kalan
ifadeler normal sohbete düşüyordu, tool tetiklemiyordu. **Bu adım artık
atıldı** (§3.3): `_RULES` sadece `get_time` için fast-path tutuyor, geri kalan
tüm araçlar Ollama native tool-calling üzerinden semantik olarak yönlendiriliyor.

Alt adımlar:
- [x] Tool arayüz şeması: `tools/base.py`'de `Tool(ABC)` — `name`,
      `description`, `risk_level`, `execute(params: dict) -> str`
      (`agents/base.py`'deki `Agent(ABC)` deseniyle simetrik). Kayıt:
      `tools/registry.py`'de statik `TOOL_REGISTRY` (dinamik keşif
      bilinçli olarak yok — hangi aracın kayıtlı olduğu tek bakışta
      görülebilmeli).
- [x] Dosya yönetimi tool'u: `tools/notes.py` (`create_note` Orta risk /
      `read_notes` Orta risk) + `tools/files.py` (`list_files`, Düşük).
      Erişim gerçekten sınırlı: her ikisi de **dışarıdan yol parametresi
      almıyor**, sabit `notes/` ve `jarvis_workspace/` dizinlerine bakıyor —
      path traversal saldırı yüzeyi hiç oluşmuyor (güvenlik incelemesi bu
      iddiayı doğruladı). Yollar proje kökünden türetilmiş **mutlak** yollar
      (`core/paths.py`) — CWD'ye bağımlı değil.
- [x] Terminal komut çalıştırma tool'u (`tools/shell.py`, `run_command`) —
      **`security-reviewer` subagent'ı ile incelendi** (aşağıya bak).
      4 katmanlı savunma: (1) `RiskLevel.HIGH` → istisnasız `[Y/N]` onayı
      (whitelist yaklaşımı bilinçle reddedildi), (2) onay isteminde komutun
      TAM METNİ gösterilir, (3) onay sorulmadan önce metin
      `OutputSafetyCheck` guardrail'inden geçer, (4) 15sn timeout +
      `taskkill /F /T` ile tüm süreç ağacının öldürülmesi.
- [x] Sistem izleme tool'u: `tools/system_info.py` (`get_system_info`,
      Düşük) — `psutil` (CPU/RAM) + `nvidia-smi` (GPU/VRAM); GPU yoksa
      sessizce sadece CPU/RAM raporluyor.
- [x] ~~**Harici API entegrasyonu — Spotify müzik kontrolü**~~ **KALDIRILDI,
      bkz. §3.3** — yerini yerel Windows medya tuşu simülasyonuna
      (`tools/media_tool.py`) bıraktı. Asağıdaki paragraf, o zamanki tasarımın
      tarihsel kaydı olarak duruyor:
      `tools/spotify.py` (`play_music`/`pause_music`/`skip_track`, üçü de
      Düşük risk — geri alınabilir, `read_notes` gibi bir gizlilik maliyeti
      yok). `spotipy` (OAuth + Web API sarmalayıcı) kullanıyor;
      credential'lar `.env`'den (`SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET`
      /`SPOTIFY_REDIRECT_URI`), koda asla gömülmeden okunuyor. **Spotify
      opsiyonel**: `.env`'de credential yoksa uygulama ÇÖKMÜYOR, sadece
      Spotify tool'ları net bir TR/EN mesajla devre dışı kalıyor (statik
      testle doğrulandı) — TTS'in zorunlu referans sesinden bilinçli olarak
      farklı bir davranış, çünkü Spotify kullanmak istemeyen bir kullanıcı
      bütün Jarvis'i kıramamalı. Yetkilendirme akışı: `python -m
      src.jarvis.tools.spotify` ile tek seferlik tarayıcı tabanlı OAuth
      (`.spotify_cache`'e yazılır, `.gitignore`'da — `.env` ile aynı
      hassasiyet); sesli komutlar `cache_handler.get_cached_token()` ile
      ÖNCE diskteki cache'e bakıyor (ağ/tarayıcı gerektirmez) — hiç
      yetkilendirme yapılmamışsa spotipy'nin sesli bir komutun ortasında
      beklenmedik şekilde tarayıcı açıp bloke olmasını önlemek için "önce
      yetkilendirin" mesajı dönüyor. Birim testleri sahte bir Spotify
      client'ıyla (gerçek ağ/hesap gerektirmeden) tüm yolları (başarı,
      cihaz yok, bulunamadı, yapılandırılmamış, yetkilendirilmemiş)
      kapsıyor.
      **Gerçek kullanım testinde bulunan iki hata, ikisi de düzeltildi:**
      (1) İlk regex kalıpları ("şarkı çal: X", "play song: X") çok dardı —
      kullanıcının gerçek iki denemesi de ("Şarkı çalın.", "Play the ...
      via Spotify?") eşleşmedi, istek Brain'e düşüp LLM şarkıyı ÇALMADIĞI
      halde "çalınıyor" diye halüsinasyon gördü. Kalıplar Türkçe fiil
      çekimlerini (çal/çalın/çalınız) ve "the"/"via Spotify" gibi dolgu
      ifadelerini kapsayacak şekilde genişletildi; dolgu temizliği
      dispatcher'da değil `tools/spotify.py:_clean_query()`'de tek yerde
      toplandı (regex'te sıralı optional group'larla yapmak kırılgan
      çıkmıştı). `system_prompt.txt`'e de bir kural eklendi: Brain artık
      müzik/not/komut gibi işlemleri kendisinin YAPMADIĞINI biliyor, bir
      istek ona ulaştıysa (dispatcher tanımadıysa) "anlamadım" diyor,
      "yaptım" diye uydurmuyor. (2) Kalıplar düzelince de gerçek testte
      Spotify AÇIKKEN bile Web API "No active device found" (404)
      döndürdü — Spotify Connect'in "aktif cihaz" kavramı sadece
      uygulamanın açık olmasıyla dolmuyor. `_with_device_fallback()`
      eklendi: `device_id` olmadan başarısız olan her playback çağrısı,
      `devices()`'ın listelediği (aktif olmasa da) bir cihazı AÇIKÇA
      hedefleyerek bir kez daha deneniyor — bu, gerçek testte cihazı
      fiilen aktif hale getirip çalmayı başlattı. İkisi de gerçek
      mikrofonla, gerçek Spotify hesabıyla uçtan uca doğrulandı.
- [ ] **Takvim entegrasyonu (Google Calendar)**: aynı gerekçeyle (OAuth
      uygulaması kaydı kullanıcının kendisi tarafından yapılmalı) bu turun
      kapsamı dışında — Spotify'daki desen (opsiyonel, `.env` tabanlı,
      credential yoksa çökmez) tekrarlanabilir, kullanıcı kendi Google
      Cloud uygulamasını kaydettiğinde ayrı bir adım olarak eklenecek.
- [ ] Opsiyonel: bu tool katmanını MCP standardına uygun bir sunucu olarak
      paketleme (bkz. `docs/claude-code-rehberi.md` §6) — hem Jarvis hem
      Claude Code aynı araçları kullanabilsin. **Not**: bu, Jarvis'i bir
      MCP SUNUCUSU yapmaktan bahsediyor; Faz 4.5'teki Hibrit MCP planı ise
      tam tersi yönde — Jarvis'in dış MCP sunucularına bir MCP İSTEMCİSİ
      olarak bağlanması. İkisi bağımsız, birbirini dışlamıyor.

**Güvenlik incelemesi bulguları ve düzeltmeleri** (`security-reviewer`,
Faz 3'ün zorunlu adımı — CLAUDE.md'nin kendi talimatı):
- [x] **Doğrulanmış atlatma**: `OutputSafetyCheck`'teki `rm -rf` ve `mkfs`
      kalıplarında `re.IGNORECASE` YOKTU — "RM -RF C:\..." sessizce
      geçiyordu. Whisper transkripsiyonu büyük/küçük harf tutarlılığı
      garanti etmediğinden bu kötü niyet olmadan da tetiklenebilirdi.
      Tüm kalıplara tutarlı `IGNORECASE` eklendi.
- [x] **Kalıp listesi Windows'u kapsamıyordu**: liste 7 POSIX-ağırlıklı
      kalıptan ibaretti; PowerShell/cmd'nin asıl yıkıcı araç seti
      (`Remove-Item -Recurse`, `rd /s /q`, `Stop-Computer`, `reg delete`,
      `diskpart`, `cipher /w`, `takeown`/`icacls`) ve LOLBAS zincirleri
      (`-EncodedCommand`, `iex`, `curl ... | bash`, `certutil -urlcache`,
      `netsh ... firewall`) hiç yakalanmıyordu. Hepsi eklendi; bayrak
      sırası/ayrımı varyantları (`rm -fr`, `rm -r -f`, `rmdir /q /s`) da
      kapsandı. Bulguların her biri için `tests/test_guardrail.py`'ye
      regresyon testi yazıldı (bu testler, düzeltmenin ilk halindeki iki
      gerçek regex hatasını — `\b`'nin tire öncesinde hiç eşleşmemesi ve
      `advfirewall`'un kaçması — anında yakaladı).
- [x] **`read_notes` bilgi ifşası**: LOW risk olduğu için FOLLOWUP
      penceresinde (wake-word gerekmeden, 12sn) herhangi bir sesle
      tetiklenip kişisel notları hoparlörden okuyabiliyordu. **MEDIUM**'a
      çekildi — risk ölçütü "eylem geri alınabilir mi" değil, "yanlış
      tetiklenmesinin bedeli ne".
- [x] **Yetim süreçler**: `subprocess.run(timeout=)` Windows'ta yalnızca
      `cmd.exe`'yi öldürüyor, onun başlattığı süreçler (`start ...`,
      `ping -t`) çalışmaya devam ediyordu. `Popen` + `taskkill /F /T` ile
      tüm süreç ağacı sonlandırılacak şekilde düzeltildi.
- [x] **Mimari varsayım belgelendi**: `shell=True`'nun güvenlik gerekçesi
      SADECE `content`'in regex'ten (LLM'e hiç uğramadan) gelmesine
      dayanıyor. İçerik çıkarımı ileride LLM'e taşınırsa bu savunma
      geçersiz olur ve prompt-injection→RCE zinciri açılır — `shell.py`
      docstring'ine kritik uyarı olarak eklendi. **Bu geçiş §3.3'te
      gerçekleşti; mitigasyonlar için oraya bakın.**

### 3.2 Zero-Trust Erişim Kontrolü 🟡 (risk+onay tamam, kimlik doğrulama bekliyor)

`docs/ARCHITECTURE.md` §6'daki risk puanlama tablosunun uygulanması:

- [x] Her tool çağrısına risk seviyesi atayan sınıflandırma:
      `core/risk.py`'de `RiskLevel` (LOW/MEDIUM/HIGH/CRITICAL). Karar
      merkezi olarak `core/app.py:_execute_tool()`'da veriliyor — bir
      aracın kendi riskini "düşük" ilan edip onaydan kaçınması imkânsız.
      `CRITICAL` tanımlı ama henüz hiçbir araç kullanmıyor (RFID yok).
- [x] `[Y/N]` onay akışı (`core/risk.py:request_approval`): Orta ve üzeri
      her aksiyon için zorunlu, **varsayılan RED** — boş girdi, tanımadık
      cevap ve `EOFError` (stdin yok) üçü de "hayır" sayılıyor (güvenlik
      incelemesi bu davranışı doğruladı). Kullanıcı ekrana bakmıyor
      olabileceği için önce sesli uyarı veriliyor, sonra terminalde
      bloklayıcı soru. Sesli onay bilinçli olarak ertelendi: STT'nin
      yanlış-algılama payı güvenlik-kritik bir yolda kabul edilemez.
- [ ] **RFID fiziksel sudo**: kök dizin erişimi/kritik donanım müdahalesi
      için `TrustElevation` modülü — **donanım gerektiriyor** (RFID
      okuyucu), o yüzden bekliyor (bkz. `docs/ARCHITECTURE.md` §6).
- [ ] **Sesli kimlik doğrulama**: konuşmacı doğrulama (ses biyometrisi).
      Güvenlik incelemesi bunun eksikliğini somut bir risk olarak işaret
      etti: FOLLOWUP penceresinde odadaki başka bir ses (TV dahil) Düşük
      riskli araçları onaysız tetikleyebilir. `read_notes`'un MEDIUM'a
      çekilmesi en hassas ifşayı kapattı, ama kalıcı çözüm bu madde.
- [ ] OWASP LLM Top 10 checklist'inin her tool/entegrasyon eklendiğinde
      tekrar gözden geçirilmesi — bu turda fiilen uygulandı
      (`security-reviewer` çağrıldı, bulguları düzeltildi ve regresyon
      testine bağlandı); süreç olarak kalıcılaştırılması sürüyor.

### 3.3 Semantic Router & Yerel "Computer-Use" Geçişi ✅

Yukarıdaki "bilinçli karar"ın (rule-based regex, §3.1) ve Spotify API
bağımlılığının tersine çevrildiği geçiş. İki bağımsız ama birlikte yapılan
dönüşüm:

- [x] **Semantic router**: `core/dispatcher.py:_RULES` sadece `get_time`
      için fast-path tutacak şekilde küçültüldü (`list_files`, `create_note`,
      `read_notes`, `run_command`, `get_system_info`, eski müzik intent'leri
      kaldırıldı). `Dispatcher.classify()` — eskiden ölü kod olan, sadece
      serbest-metin bir intent adı döndüren hali — tamamen yeniden yazıldı:
      artık `AgentFactory.create("orchestrator")` (yerel `llama3.1:8b`) ile
      Ollama'nın **native tool-calling** arayüzünü (`ollama.chat(...,
      tools=[...])`) kullanıyor, `TOOL_REGISTRY`'deki her aracı
      `adapters/tool_schema.py:build_ollama_tools()` ile bir JSON-Schema
      function tanımına çeviriyor. Model artık serbest metin değil,
      structured `tool_calls` (isim + argümanlar) döndürüyor — parse
      belirsizliği yok. `agents/base.py`'ye `ToolCall`/`AgentToolResponse` +
      `Agent.call_tools()` eklendi (mevcut `respond()`'a dokunulmadı).
      **Bilinen maliyet**: rule-eşleşmeyen her turda artık (router + varsa
      Brain) iki ayrı LLM çağrısı olabiliyor — kabul edilen bir trade-off.
      **Gelecek iyileştirme adayı**: router+chat'i tek streaming çağrısına
      birleştirmek, veya daha küçük/hızlı bir router modeli (örn.
      `llama3.2:3b`) kullanmak.
- [x] **API'siz yerel araçlar**: `tools/spotify.py` (spotipy, OAuth, ağ
      bağımlılığı) tamamen kaldırıldı; yerine `tools/media_tool.py` geldi —
      `ctypes.windll.user32.SendInput` ile Windows sanal medya tuşları
      (`VK_MEDIA_PLAY_PAUSE`/`NEXT_TRACK`/`PREV_TRACK`/`VK_VOLUME_*`), yeni
      pip bağımlılığı yok. `tools/notes.py` → `tools/notes_tool.py`: artık
      proje içi `notes/notes.txt` yerine kullanıcının gerçek Obsidian
      vault'una (`config/security.yaml:obsidian_vault`) sabit bir
      `Jarvis Notes/Jarvis Log.md` dosyası olarak yazıyor/okuyor (dosya adı
      LLM parametresinden asla gelmiyor — vault'ta keyfi dosyaya
      yazma/silme riskini kapatan bilinçli bir kapsam sınırlaması).
      `tools/shell.py` → `tools/terminal_tool.py`: aynı `RunCommandTool` +
      yeni `LaunchAppTool` (`config/security.yaml:known_applications`
      allowlist'inden bir uygulama başlatır, MEDIUM risk).
- [x] **`core/security_config.py` (yeni)**: `config/security.yaml`'dan
      (kişisel/makineye özel, `.gitignore`'da — şablonu
      `security.example.yaml` commit'lenir) `allowed_directories`,
      `known_applications`, `obsidian_vault` okuyor. `is_path_safe()` —
      `Path.resolve()` + `Path.is_relative_to()` ile path traversal ve
      symlink-kaçışını engeller (string-prefix karşılaştırması BİLİNÇLİ
      OLARAK kullanılmadı — kardeş dizin yanlış-pozitifi riski).
- [x] **Güvenlik geçişinin somut mitigasyonu** (§3.1'deki "mimari varsayım"
      bulgusuna yanıt — artık `run_command`'ın `command`'ı LLM tarafından
      üretiliyor): `core/app.py:_execute_tool`, `intent.parameters`'taki
      `lang` hariç TÜM string değerleri (sadece `content`'i değil)
      `OutputSafetyCheck`'ten geçiriyor; `core/console.py:print_approval_panel`
      (yeni, `rich.Panel`) onay ekranında LLM'in ürettiği TAM parametreleri
      büyük ve net gösteriyor — kullanıcı kendi söylediğinden farklı bir
      argümanı onaylıyor olsa bile bunu görüyor; `print_router_decision`
      (yeni) hangi aracın seçildiğini şeffaflaştırıyor (sadece
      `source=="llm"` ve bir araç gerçekten seçildiğinde gösteriliyor, düz
      sohbette gürültü yapmıyor). `launch_app` ayrıca allowlist ile
      sınırlandırılarak LLM'in keyfi bir path/komut üretmesi engellendi.
      Bu geçiş sonrası `terminal_tool.py`/`dispatcher.py`/`app.py`
      `security-reviewer` subagent'ı ile incelendi (bulgular varsa ayrı bir
      düzeltme commit'i olarak ele alındı — bkz. proje geçmişi).

## Faz 3.4 — JARVIS HUD (Web Arayüzü) ✅

Sesli/terminal döngüsüne PARALEL, salt-izleyici + tek yönlü-komut bir
web arayüzü — mevcut hiçbir güvenlik/onay akışını atlamıyor (bkz. altta).

- [x] **`core/hud_bus.py`** (yeni): thread-safe pub/sub — projenin geri
      kalanı (Ears/Mouth/core/app, hepsi senkron) `publish_log`/
      `publish_state`/`publish_telemetry`/`publish_tool` çağırır,
      `asyncio` bilmez. `core/console.py`'nin HER `print_*` fonksiyonu
      (zaten "tüm terminal çıktısı buradan geçmeli" ilkesiyle tek
      merkeziydi) artık aynı zamanda buraya da yayınlıyor — web arayüzü
      terminalle AYNI çıktıyı, tek bir dokunuşla onlarca çağrı noktasında
      görüyor.
- [x] **`core/telemetry.py`** (yeni): `psutil` + opsiyonel `nvidia-smi` —
      CPU/RAM/GPU/ağ. `tools/system_info.py` (sesli "sistem durumu")
      ile AYNI fonksiyonları paylaşıyor (DRY). Sahte veri yok ilkesi:
      gerçek karşılığı olmayan alanlar (ör. "sıcaklık") hiç üretilmiyor.
- [x] **`core/api.py`** (yeni): FastAPI + WebSocket (`/ws`) köprüsü.
      `main.py` bunu **ayrı bir daemon thread'de** başlatıyor
      (`start_api_server_thread()`) — uvicorn'un kendi asyncio event
      loop'u, Ears/Mouth/Brain'in senkron/bloklayıcı ana thread'ini HİÇ
      etkilemiyor. Güvenlik: sunucu sadece `127.0.0.1`'e bağlanıyor;
      WebSocket handshake'inde `Origin` başlığı elle doğrulanıyor
      (`CORSMiddleware` WebSocket'i KORUMAZ — bkz. modül docstring'i) ki
      açık bir sekmedeki kötü niyetli bir site bu soket'e bağlanıp komut
      sokamasın. Yazılı komutlar `InputHub.submit_external_text()`
      üzerinden terminal girdisiyle AYNI kuyruğa/guardrail/onay zincirine
      giriyor — web'den ekstra/onaysız bir yetenek AÇILMIYOR.
- [x] **`web-ui/`** (yeni, React + TypeScript + Vite + three.js +
      framer-motion): kehribar (#FFBF00) temalı retro-fütüristik HUD —
      durumlara (idle/listening/processing/speaking) tepki veren 3B
      holografik parçacık küresi, daktilo-efektli sistem konsolu (komut
      girişi dahil), CPU/RAM/GPU halka göstergeleri, geçici araç-kullanım
      bildirimleri, CRT tarama çizgisi/vinyet katmanları. Ayrıntı için
      `web-ui/README.md`.

## Faz 4 — Otonom Ajan Döngüsü ⬜

Alt adımlar:
- [ ] Görev planlama/zincirleme: çok adımlı bir isteği alt görevlere bölme.
- [ ] Kısa vadeli hafıza (oturum içi context) vs uzun vadeli hafıza (disk/DB
      üzerinde kalıcı tercih/bağlam) ayrımı.
- [ ] Hata kurtarma: bir adım başarısız olursa yeniden dene / kullanıcıya
      sor / alternatif plana geç.
- [ ] Çok adımlı yürütme döngüsü: plan → araç çağrısı → sonucu değerlendir →
      devam et/bitir.
- [ ] Kullanıcı onay noktaları: riskli aksiyonlardan (dosya silme, dış API'ye
      veri gönderme) önce onay iste — Faz 3.2'deki risk puanlama/onay
      akışıyla örtüşür, aynı mekanizma kullanılır.

## Faz 4.5 — Hibrit MCP Entegrasyonu (Bilgi Katmanı) 🟡 (altyapı + File System MCP tamam, SQLite/GitHub/Claude Code CLI bekliyor)

Faz 3'ün Zero-Trust felsefesiyle tutarlı bir hibrit karar: işletim sistemi
kontrolü (terminal, uygulama başlatma, medya) YÜKSEK riskli olduğu için
her zaman yerel `TOOL_REGISTRY`/`security.yaml` sandbox'ında kalır; MCP
(Model Context Protocol) yalnızca geniş bilgi/veri erişimi için devreye
girer. Mimari gerekçe ve tasarım detayı için `docs/ARCHITECTURE.md` §9'a
bak — burada sadece eyleme geçirilebilir adımlar var.

Alt adımlar:
- [x] `MCPClientAdapter` (`src/jarvis/adapters/mcp_client_adapter.py`) —
      MCP sunucularını keşfedip araçlarını `adapters/tool_schema.py:
      build_ollama_tools()` ile aynı şemaya çevirir. **Kritik tasarım
      kararı**: statik `tools/registry.py:TOOL_REGISTRY` (bilinçli olarak
      auto-discovery'siz) ile MCP'nin dinamik doğası asla birleştirilmez —
      MCP araçları `tools/registry.py:all_tools()`/`get_tool()` view'i
      üzerinden sunulur, hiçbir zaman sessizce `TOOL_REGISTRY`'ye enjekte
      edilmez; varsayılan risk seviyesi en az `MEDIUM` (dış sunucu verisi =
      güvenilmeyen girdi, `LOW` istekleri `core/mcp_config.py` tarafından
      otomatik `MEDIUM`'a yükseltilir). Async (mcp SDK) ↔ senkron (proje)
      köprüsü, arka planda kalıcı TEK bir event-loop thread'i + tek bir
      uzun-ömürlü `_serve()` coroutine'i (async context manager'ların
      anyio cancel-scope'larını AYNI Task içinde açıp kapatması gerektiği
      gerçek testte bulunup düzeltildi) ile kuruldu. `core/app.py:
      _execute_tool()` artık tool'un DÖNÜŞ değerini de `OutputSafetyCheck`'ten
      geçiriyor (eskiden sadece girdi taranıyordu) — MCP'nin dış/güvenilmeyen
      verisi için eklenen bir sertleştirme, yerel araçları etkilemiyor.
- [x] `config/mcp_servers.yaml` + `config/mcp_servers.example.yaml` —
      **fail-soft** yükleme (bilinçli sapma, `docs/ARCHITECTURE.md` §9.2'de
      düzeltildi): MCP, Spotify gibi opsiyonel bir katman — dosya yoksa/
      hiçbir sunucu etkin değilse `core/mcp_config.py:load_mcp_servers_config()`
      net bir uyarı loglar ve boş liste döner, `security_config.py`'nin
      `FileNotFoundError`'ı FIRLATILMAZ (uygulama MCP'siz de çalışmaya devam
      eder).
- [x] **File System MCP** — Obsidian vault'un (`security.yaml:
      obsidian_vault`) geniş, arama yapılabilir okunması; resmi
      `@modelcontextprotocol/server-filesystem` (npx ile, ek pip bağımlılığı
      yok) `allowed_tools` allowlist'iyle SADECE okuma araçlarına
      (`read_text_file`, `list_directory`, `directory_tree`, `search_files`,
      `get_file_info`, vb.) sınırlandı — `write_file`/`edit_file`/
      `create_directory`/`move_file` bilinçli olarak dışarıda, `tools/
      notes_tool.py`'nin dar-yazma felsefesiyle tutarlı. Gerçek `npx` ile
      uçtan uca doğrulandı (`python -m src.jarvis.adapters.mcp_client_adapter`
      + `get_tool("mcp_filesystem_list_directory").execute(...)`).
      **`security-reviewer` incelemesi** (CLAUDE.md'nin zorunlu adımı):
      2 kritik bulgu düzeltildi — (1) MCP sonucu için guardrail taraması
      eskiden kırpılmış/yer-tutucu metin üzerinde çalışıyordu, gerçek içerik
      hiç taranmıyordu (artık `MCPTool.execute()` HAM içeriği kırpma/konsola
      basmadan önce tarıyor); (2) MCP sunucusunun `name`/`description`/
      parametre açıklamaları filtresiz router LLM'ine gidiyordu ("tool
      poisoning" — artık keşif anında `InputInjectionCheck`'ten geçiyor,
      takılan araç sessizce atlanıyor). Ayrıca: `call_tool()` zaman
      aşımında artık future/Task iptal ediliyor (kaynak sızıntısı
      düzeltmesi), `allowed_tools` `call_tool()` içinde de ikinci kez
      doğrulanıyor (derinlemesine savunma), npm paketi sürüm-PİNLENDİ
      (`@modelcontextprotocol/server-filesystem@2026.7.10`), konsola
      basılan MCP içeriğinden kontrol karakterleri temizleniyor (terminal
      enjeksiyonu önlemi). Açık kalan bulgular (path traversal için ikinci
      katman doğrulama yok, senkron `call_tool()` ana döngüyü ~30sn
      bloklayabiliyor) bilinçli olarak kapsam dışı bırakıldı — bkz.
      `docs/ARCHITECTURE.md` §9.5 "Bilinen açık maddeler".
- [ ] **SQLite MCP** — "Asistan Game Master" modu: FRP zar mekanikleri,
      karakter statları, kural kitapçıkları için yapısal sorgu erişimi;
      Faz 4'ün çok adımlı yürütme döngüsüyle örtüşür. **Engel**: resmi
      referans sunucu `uvx mcp-server-sqlite` gerektirir, bu makinede
      `uv`/`uvx` kurulu değil (`npx`'in aksine) — kuruluma ek bir adım.
      `config/mcp_servers.example.yaml`'da şablon olarak duruyor.
- [ ] **GitHub MCP** — yazılım ve Godot proje yönetimi için repo okuma, PR
      analizi, commit farkları; salt-okunur öncelikli, yazma işlemleri
      (merge, issue kapama) yerel `RiskLevel.HIGH` ile aynı zorunlu `[Y/N]`
      onayından geçmeli.
- [ ] **Claude Code CLI "Alt Yüklenici" tetikleme deseni** — mevcut
      `ClaudeCodeAdapter`'ın (Faz 2.2, şu an `NotImplementedError` stub)
      doğrudan Anthropic API yerine `terminal_tool`/`run_command` (zaten
      HIGH risk + onay akışında) üzerinden `claude` komutunu çalıştıracak
      şekilde revize edilmesi — ayrı bir API entegrasyonu/credential
      yönetimi gerekmez.
- Not: **IoT MCP** entegrasyonu Faz 5.1'e ait (aşağı bak) — mevcut
  MQTT/VLAN mimarisinin yerine değil, onunla birlikte düşünülecek.

## Faz 5 — IoT Entegrasyonu & Dağıtım ⬜

`docs/ARCHITECTURE.md` §8'deki mimarinin uygulanması.

### 5.1 IoT & Uç Nokta (Client) Yönetimi

- [ ] İstemci mimarisi: dizüstü bilgisayar, akıllı telefon gibi cihazlar
      ana sunucuya bağlı birer "client" (uç nokta) olarak görev yapar; bu
      cihazlarda asistanlık ve yetki onayı (Telegram Inline Keyboard veya
      yerel API) sağlanır.
- [ ] Ağ izolasyonu: Jarvis'in kontrol edeceği IoT cihazları ana ev
      ağından izole edilmiş bir VLAN'da bulunur, haberleşme şifreli MQTT
      protokolü üzerinden yapılır (TLS + broker kimlik doğrulama).
- [ ] MQTT broker seçimi ve topic/izin şeması (hangi client hangi topic'e
      publish/subscribe edebilir).
- [ ] IoT MCP sunucuları — ESP32/mikrodenetleyici sistemlerinin cihaz
      durumu/telemetri OKUMASI için MCP üzerinden bağlanması (bkz. Faz 4.5
      ve `docs/ARCHITECTURE.md` §9.3); fiziksel aktüasyon komutları bu
      yoldan DEĞİL, yukarıdaki MQTT + Zero-Trust risk puanlamasından geçer.

### 5.2 Dağıtım, Arayüz ve Taşınabilirlik

- [ ] Uygulama Docker (veya benzeri konteyner teknolojisi) ile paketlenir —
      GPU passthrough (CUDA) desteği dahil.
- [ ] Kompakt bir arayüz (UI) veya sistem tepsisi (system tray) uygulaması
      ile arka planda çalışacak şekilde tasarlanır.
- [ ] Kurulum/taşıma dokümantasyonu: başka bir makineye (RTX 4070 dışında
      bir GPU dahil) taşınırken hangi adımların (bkz. `CLAUDE.md` Komutlar
      bölümü) tekrarlanması gerektiği netleştirilir.

---

## Notlar

- Bu dosya `CLAUDE.md`'nin 200 satır kısıtından muaf; detay burada birikir.
  Mimari tasarımın "nasıl/neden"i için `docs/ARCHITECTURE.md`'ye bak.
- Her adım tamamlandığında durum etiketini (✅/🟡/⬜) güncelle, böylece
  `CLAUDE.md`'deki kısa özetle senkron kalır.
- Yeni bir adıma başlarken önce **plan mode** ile keşif yap (bkz. rehber
  `docs/claude-code-rehberi.md` §7 "Günlük döngü").
