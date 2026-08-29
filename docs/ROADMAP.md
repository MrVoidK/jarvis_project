# Jarvis — Detaylı Yol Haritası (Faz 1–6)

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
- [x] **İlk kullanım sonrası düzeltmeler**: gösterge halkaları sabit
      piksel yerine responsive (panelde artık taşmıyor); `HologramOrb`
      kamera/ölçek değerleri kameranın 3B görüş alanını (frustum) aşıp
      "kırpılma" yaratıyordu — kamera geri çekildi, geometriler küçültüldü,
      tüm pulse/rotasyon hızları yavaşlatılıp sürekli bir "breath" katmanı
      eklendi (organik his). Paneller düz tek-renk + sert kenarlık yerine
      köşe-braket + gradyan dolgu + yavaş "sheen" geçişi kullanıyor.
      `useJarvisSocket` artık modül-seviyesi bir singleton'a
      (`lib/jarvisSocketManager.ts`) bağlanıyor — React StrictMode'un
      geliştirme modu çift-mount'u eskiden aynı sayfada birden fazla
      WebSocket aboneliğine (ve dolayısıyla tekrarlanan log satırlarına)
      yol açabiliyordu. `print_table` artık sütun başlıklarını da
      `hud_bus`'a gönderiyor, `Terminal.tsx` çok-satırlı/tablo içeriğini
      (ör. `/help`) ayrı bir blok + gerçek HTML tablosu olarak (satır
      kaydırmalı, taşmadan) render ediyor. `core/web_ui_process.py`
      (yeni): `main.py` artık `web-ui`'nin Vite dev sunucusunu otomatik
      bir alt-süreç olarak başlatıyor ve kapatırken (Ctrl+C) Windows'ta
      `npm` → `cmd.exe` → `node.exe` süreç ağacının TAMAMINI
      (`taskkill /T /F`) kapatıyor — aksi halde yetim bir `node.exe`
      arka planda çalışmaya devam ediyordu.

## Faz 4 — Otonom Ajan Döngüsü ⬜

Alt adımlar:
- [ ] Görev planlama/zincirleme: çok adımlı bir isteği alt görevlere bölme.
- [ ] Kısa vadeli hafıza (oturum içi context) vs uzun vadeli hafıza (disk/DB
      üzerinde kalıcı tercih/bağlam) ayrımı — bkz. Faz 6.5 (Mem0 tabanlı kalıcı
      hafıza değerlendiriliyor, karar verilmedi).
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
      yönetimi gerekmez. Detaylandırma: Faz 6.3 (v2 Faz C) — router'a
      `delegate_code_task` sentinel'i + `_handle_turn()` dalı orada eklenir.
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

## Faz 6 — Multi-Agent Mimarisi v2 (Rol Konsolidasyonu + Execution Modes + Gözlemlenebilirlik) 🟡 (6.1-6.6 tamam, 6.7+ bekliyor)

`docs/jarvis-mimari-v2-multiagent-entegrasyon.md`'deki "Faz A–I" planının
ROADMAP numaralandırmasına taşınmış hâli — mimari gerekçe ve tam spec o
dokümanda, "nasıl/neden" için ayrıca `docs/ARCHITECTURE.md` §4–5 (senkronize
edildi) ve yeni §10–13'e bak; burada sadece eyleme geçirilebilir adımlar var.
Bu faz, `docs/mimari-genel-bakis.md` §20 "Bilinen Sınırlamalar" listesindeki
maddeleri somut kod değişikliklerine çevirir ve multi-agent/hafıza/execution-
mode genişlemesini **aynı** güvenlik felsefesiyle (Zero-Trust, fail-closed,
tek merkezi güvenlik hattı, fail-soft dış bağlantılar) ekler.

**Bağımlılık sırası** (v2 §13): 6.1 → 6.2 → 6.3; 6.7 hem 6.1'e (sertleştirilmiş
`is_path_safe`) hem 6.3'e bağlı; 6.4 → 6.6 (risk-kısıt kuralı); 6.5 ve 6.6
birbirinden bağımsız; 6.8 ve 6.9 tamamen bağımsız, herhangi bir zaman;
**6.10 → 6.3 + 6.4 + 6.5'e sıkı** (6.5 `recall()` senaryo #2 için), 6.8'e
yumuşak (Calendar MCP), Faz 6 capstone'u. Her alt faz kendi commit'i/PR'ı
olmalı.

| ROADMAP | v2 Faz | v2 § | Kısa |
|---|---|---|---|
| 6.1 | A | §7.1–7.2 | `Tool.execute()` `stop_event` + `is_path_safe()` sertleştirme |
| 6.2 | B | §2.2–2.4 | Tek paylaşımlı model + `respond_stream()` + mini router |
| 6.3 | C | §2.5–2.6 | `ClaudeCodeAdapter` (run_command) + delegasyon sentinel'leri |
| 6.4 | D | §3 | Agent Registry / allowlist manifest |
| 6.5 | E | §4 | Kalıcı semantic hafıza — DIY (sentence-transformers + SQLite), Mem0 DEĞİL |
| 6.6 | F | §5 | Execution modes — scheduled + continuous |
| 6.7 | G | §6 | `CreateProjectTool` + `spawn_detached()` |
| 6.8 | H | §8 | MCP genişletme — Google Drive + Home Assistant |
| 6.9 | I | §9 | Gözlemlenebilirlik — `core/trace.py` + `/trace` |
| 6.10 | — | §2.6 + §3 + §4 | Akıllı Aksiyon Katmanı — genel orkestrasyon döngüsü + uzman tool-set'ler + mutasyon yetkili delegasyon |

### 6.1 Önkoşul Sertleştirmeleri (v2 Faz A) ✅

Küçük, izole, bağımsız iki değişiklik — 6.6 ve 6.7 bunlara bağlı olduğu için
önce yapıldı.

Alt adımlar:
- [x] **`Tool.execute()` imzası** — `tools/base.py:Tool.execute()` ABC'sine
      geriye uyumlu `stop_event: threading.Event | None = None` parametresi
      eklendi (gelecek araçlar için işbirlikçi-iptal sözleşmesi). 13 somut
      alt-sınıf imzası DEĞİŞMEDİ.
- [x] **Zorlayıcı iptal (timeout sarmalayıcı)** — `core/app.py:_run_tool_pipeline()`
      `tool.execute()`'ı `concurrent.futures.ThreadPoolExecutor` + `future.result(
      timeout=_TOOL_EXEC_TIMEOUT_SECONDS)` (30 sn) ile ayrı bir worker thread'de
      çalıştırır; zaman aşımında lokalize `_TOOL_TIMEOUT_MESSAGES` döner. Ana
      döngü artık bir tool tarafından süresiz bloklanamaz.
- [x] **`is_path_safe()` sertleştirme** — `core/security_config.py`: UNC
      (`\\server\share`) + aygıt ad-alanı (`\\?\`, `\\.\`) önekleri artık her
      zaman reddediliyor (`_has_unsafe_prefix`); opt-in `allow_create` keyword'ü
      (`False` → yol diskte var olmalı); ayrı `is_safe_component_name()` helper
      (LLM-türevli tek yol bileşeni için karakter allowlist'i). Docstring'deki
      "ileride eklenmelidir" notu kapandı.

**v2 §7.1–7.2'den bilinçli sapmalar:** (1) `_run_tool_pipeline` `stop_event`
kwarg'ını araçlara GEÇİRMİYOR — "araçlar hemen değişmek zorunda değil"
garantisi korundu; zorunlu iptali dış timeout sarmalayıcı sağlıyor. `future.
result(timeout)` çalışan thread'i durduramaz (kabul edilen sınır, kod yorumunda).
(2) İmza `is_path_safe(path, config=None, *, allow_create=True)` — mevcut
`allowed_directories` (liste) modeli ve `config=` kullanan çağrılar korundu;
`allow_create` varsayılanı v2'deki `False`'tan `True`'ya çevrildi (geriye
uyumluluk). (3) Ad-allowlist'i `is_path_safe` içinde değil ayrı
`is_safe_component_name()` helper'ında (SRP + test edilebilirlik).

### 6.2 Model Konsolidasyonu & Brain Refactor (v2 Faz B) ✅

VRAM bütçesi (`docs/mimari-genel-bakis.md` §20 madde 12), çift-LLM-çağrısı
gecikmesi (madde 1) ve "sohbet yolu `Agent` arayüzünü kullanmıyor" (madde 2)
sorunlarını aynı anda çözdü.

Alt adımlar:
- [x] **Tek paylaşımlı model** — `orchestrator` ve `tool_agent` rolleri aynı
      `hermes3:8b`'yi paylaşır; `llama3.1:8b` bırakıldı.
      `adapters/agent_factory.py:ROLE_MODEL_MAP` eklendi;
      `LlamaOrchestratorAdapter` → `OllamaAgentAdapter` (çok-rollü, `model_name`
      parametreli); `HermesAgentAdapter` silindi.
- [x] **`respond_stream()`** — `agents/base.py:Agent` ABC'ye somut varsayılan
      metod eklendi (`yield self.respond(...)`), `OllamaAgentAdapter` gerçek
      streaming'le override ediyor. `brain/llm.py:think_and_respond_stream()`
      artık `AgentFactory.create("orchestrator").respond_stream()` üzerinden;
      cümle bölme / `history` / hata sınıflandırması `brain/llm.py`'de kaldı.
- [x] **Mini router modeli** — `ROLE_MODEL_MAP["router"] = "qwen2.5:3b"`;
      `core/dispatcher.py:Dispatcher.classify()` `AgentFactory.create("router")`
      kullanıyor. `main.py` boot'ta hem `hermes3:8b` hem `qwen2.5:3b` doğruluyor.
- [x] **Regresyon + canlı doğrulama** — `python -m pytest tests/` 125 yeşil
      (+`tests/test_brain_llm.py` yeni). `verify-brain-pipeline`: EN girdi → EN
      yanıt, tek cümle, markdown yok, 2. çağrıda hafıza (hermes3:8b). Canlı
      router testi (`qwen2.5:3b`, 11 girdi): 11/11 doğru — 4 düz sohbet (TR+EN)
      → `chat` (spurious tool yok), tool komutları doğru araca.
- [x] **`no_tool_needed` sentinel yeniden-testi** — `_NO_TOOL_SCHEMA` şemadan
      çıkarılıp 5 düz sohbet girdisi `qwen2.5:3b`'ye verildi → hepsi boş
      `tool_calls` döndü. Yani `qwen2.5:3b`'de `llama3.1:8b`'nin "hep bir araç
      çağır" şablon önyargısı YOK; sentinel **bu modelle gereksiz olabilir**.
      Ama 6.2'de KALDIRILMADI (zararsız + `hermes3`/`llama3.1`'e geri dönülürse
      hâlâ gerekli) — sentinel temizliği ayrı bir gelecek adım.
- **VRAM notu:** `hermes3:8b` (~5 GB) + `qwen2.5:3b` (~2.2 GB) + Whisper (~2) +
      XTTS (~2.5) ≈ 11.7-12.6 GB, 12 GB sınırına çok yakın. Ollama'nın
      `keep_alive` (5 dk) boşta modeli tahliye ettiği için pratikte 4'ü aynı
      anda hot olmuyor; yine de canlı `python main.py` oturumunda `nvidia-smi`
      izlenmeli (ARCHITECTURE.md §5 öneri #2: sıralı yükleme fallback'i).

**v2 §2.2–2.4'ten bilinçli sapmalar:** (1) Adapter'a `role_prompt` ctor param'ı
EKLENMEDİ — sistem/rol promptu çağıran taraftan `context` ile geliyor (mevcut
tasarım); `role_prompt`, `tool_agent`'ın kendi persona'sına ihtiyaç duyduğu
Faz 6.3'te eklenecek. (2) `respond_stream()` ABC'de abstract değil somut
varsayılan (native streaming'i olmayan adapter tek-parçaya düşer). (3)
`respond_stream()` sağlayıcı hatalarını YUTMAZ (propagate) — `respond`/`call_tools`
hata-string döner ama streaming tüketicisi `brain/llm.py` kendi TR/EN + history
mantığına sahip (v2 §2.4). (4) `LlamaOrchestratorAdapter` → `OllamaAgentAdapter`
yeniden adlandırıldı.

### 6.3 Multi-Agent Aktivasyonu (v2 Faz C) ✅

`docs/ARCHITECTURE.md` §4 iletişim şemasını kağıt üstünden koda geçirdi.

Alt adımlar:
- [x] **`ClaudeCodeAdapter` gerçek implementasyon** — `agent_factory.py`:
      `subprocess.Popen(["claude", "-p", prompt], cwd=PROJECT_ROOT)` +
      `communicate(timeout=120s)` + timeout'ta `_kill_process_tree` (Windows
      `taskkill /F /T`, `terminal_tool.py` deseninin 3. kopyası). **anthropic
      SDK / `ANTHROPIC_API_KEY` yolu UYGULANMADI** (kullanıcı kararı).
      `claude -p` VARSAYILAN izinlerle: salt-okuma (yazma/bash `-p` modunda
      otomatik reddedilir). `supports_tools()` → `False`, `call_tools()` →
      `NotImplementedError`. Canlı doğrulandı (nested `claude` oturumu sorunsuz).
- [x] **Delegasyon sentinel'leri** — `core/dispatcher.py`: `_DELEGATE_COMPLEX_SCHEMA`
      / `_DELEGATE_CODE_SCHEMA` router şemasına eklendi; `classify()` bunları
      `Intent("delegate_complex" | "delegate_code", 0.7, parameters={"task": ...})`'e
      eşler (`_NO_TOOL` kontrolünden sonra, `get_tool`'dan önce; `validate_arguments`
      yok — `Tool` değil, `task` str'e zorlanıp ham girdiye fallback).
      `_ROUTER_SYSTEM_PROMPT`'a somut örnekler eklendi (qwen2.5:3b `delegate_complex`'i
      yeterince tetiklemiyordu — canlı test bulgusu).
- [x] **`_handle_turn()` dalları** — `core/app.py`: `SHUTDOWN` kontrolünden sonra
      iki `yield from` dalı. `_run_delegate_complex` → `tool_agent` (`hermes3:8b`)
      ile sınırlı ≤`_MAX_DELEGATE_STEPS`(3) döngü; her adım mevcut `_execute_tool`
      (onay + guardrail + timeout + HUD) üzerinden — **yeni güvenlik yüzeyi yok**.
      `_run_delegate_code` → `_prompt_for_approval` (yeni helper, `_run_tool_pipeline`
      step 2'den çıkarıldı) + "biraz sürebilir" anonsu + `ClaudeCodeAdapter.respond()`
      (TTS-dostu kalması için prompt'a "≤3 spoken sentences, no markdown" eklenir).
- [x] **VRAM optimizasyonu** — `OllamaAgentAdapter` `keep_alive` param'ı;
      `AgentFactory.create("router")` → `keep_alive="2m"` (`qwen2.5:3b` konuşma
      bitince ~2 dk sonra VRAM'den çıkıp ~2.2 GB serbest bırakır, aktif konuşmada
      hot kalır).
- [x] **Testler + canlı doğrulama** — `pytest tests/` 139 yeşil (+14 yeni).
      Canlı router (11 girdi): 10/11 — düz sohbet + basit tool'lar regresyonsuz,
      `delegate_complex` 2/2, `delegate_code` 1/2 ("...analiz et ve hataları bul"
      → chat; qwen2.5:3b'nin "analiz" kelimesini soru sanma eğilimi, kabul edilen
      3B sınırı — "refactor..." doğru gidiyor).

**v2 §2.5–2.6'dan bilinçli sapmalar:** (1) `ClaudeCodeAdapter` `claude -p` alt
süreç, anthropic SDK/key DEĞİL (kullanıcı kararı). (2) `claude -p` salt-okuma
(dosya DEĞİŞTİREN mutasyon yetkili mod, Zero-Trust iki-aşamalı onayla →
**Faz 6.10.3**). (3) `delegate_code` `_run_command`
Tool'una değil doğrudan `_handle_turn` dalına bağlı — ama yine de `_prompt_for_approval`
(HIGH) kapısından geçiyor; blokaj ~120 sn (kabul edilen sınır, non-blocking varyant
Faz 6.7). (4) `tool_agent` döngüsü `role_prompt` ctor param'ı yerine
`_TOOL_AGENT_SYSTEM_PROMPT`'u `app.py`'den context olarak alıyor (6.2 kararı).

### 6.4 Agent Registry / Manifest Sistemi (v2 Faz D) ✅

Mevcut "bir araç yanlışlıkla kayıtlı olamaz" güvenlik ilkesini bozmadan
dinamik araç/ajan ekleme. Otomatik keşif DEĞİL — iki elle adım gerekir
(manifest koymak + allowlist'e ad eklemek).

Alt adımlar:
- [x] **Manifest şeması** — `agents/registry/*.yaml` (alanlar: `name`,
      `description`, `kind`, `risk_level`, `execution_mode`, `module`, `class`,
      `parameters_schema`). Şablon: `agents/registry/README.md` +
      `home_assistant_lights.example.yaml`.
- [x] **Yükleyici** — `core/registry_loader.py:load_dynamic_tools()` yalnızca
      adı `config/security.yaml:enabled_dynamic_agents` allowlist'inde olan
      manifest'i yükler; diğerleri sessizce atlanır (fail-closed).
- [x] **Üç kaynak** — `tools/registry.py:all_tools()` statik `TOOL_REGISTRY` +
      `load_dynamic_tools()` + MCP keşfini birleştirir; hiçbiri sessizce
      `TOOL_REGISTRY`'ye enjekte edilmez.
- [x] `config/security.example.yaml`'a `enabled_dynamic_agents: []` eklenir.

Uygulama notları / v2 §3'ten bilinçli sapmalar:
- **Metadata otoritesi sınıfta:** `module:class` instantiate edilir; `Tool`
  alt sınıfı statik araçlarla aynı self-describing sözleşmeyi taşır. Manifest
  `name`/`risk_level` sınıfla uyuşmazsa manifest **fail-closed atlanır** (bir
  manifest gerçekte HIGH olan bir sınıfı "MEDIUM" diye kayda geçiremez);
  `description`/`parameters_schema` sürüklenmesi yalnızca uyarı loglar.
- **Öncelik statik > dinamik manifest > MCP** ve statik ad çakışmasında her
  zaman kazanır — v2 §3.3'teki `{**TOOL_REGISTRY, **dynamic, **mcp}` kod
  parçacığının aksine (o sıralamada MCP statiği ezerdi).
- **Allowlist anahtarı = dosya kökü (stem)**, manifest içindeki `name` değil
  (v2 §3.3 yorumuyla tutarlı). `*.example` stem'leri de atlanır.
- **execution_mode risk kapısı 6.4'te uygulandı** (v2 §5.3): `scheduled|
  continuous` + `risk_level` MEDIUM+ → boot'ta reddedilir.
- **Per-manifest fail-soft** (`mcp_config.py` deseni): bozuk/uyumsuz manifest
  uyarı + `print_system` ile atlanır, Jarvis yine başlar. `import_module`
  yalnızca `ImportError` değil **her import-zamanı istisnasını** yakalar
  (`SyntaxError`, keyfi `raise`) — security-reviewer bulgusu.
- **`risk_level: critical` reddedilir** (v2 §10: CRITICAL/RFID kapsam dışı,
  `TrustElevation` yok — dinamik yol kod-inceleme kapısını atladığı için).
- Ek sertleştirmeler: `.example` kontrolü case-insensitive; `description`
  `[:500]` kırpılır (router bağlamı); `enabled_dynamic_agents` skaler verilirse
  yok sayılır; `_dynamic_tools_cache` `threading.Lock` ile korunur.
- Testler: `tests/test_registry_loader.py` (+25), `tests/test_registry_merge.py`
  (+5), `tests/test_security_config.py` (+3). `pytest tests/` 169 yeşil.
  `security-reviewer` subagent: tasarım sağlam, 1 uyarı (import fail-soft) +
  5 öneri — hepsi uygulandı.

### 6.5 Kalıcı Semantic Hafıza Katmanı (v2 Faz E) ✅

**Karar verildi — Mem0 DEĞİL, DIY minimal.** `sentence-transformers`
(`paraphrase-multilingual-MiniLM-L12-v2`, CPU — Jarvis iki dilli olduğu için
İngilizce-ağırlıklı `all-MiniLM-L6-v2` yerine; aynı 384-boyut) + merkezi
`data/jarvis.db` (SQLite, WAL) + in-process numpy brute-force cosine.

- **Mem0 reddedildi:** her `remember()`'da fact-extraction için ekstra Ollama
  LLM turu (12 GB VRAM bütçesiyle çakışır — hermes3+qwen+whisper+xtts),
  `openai` bağımlılığı, v2.x hızlı değişen API; tek-kullanıcı ölçeğinde
  fact-dedup değeri marjinal. **Yeniden değerlendirme tetiği:** ham-cümle
  hafızası çok gürültü/tekrar üretirse.
- **sqlite-vec reddedildi:** Windows pip wheel'i yok (Mart 2026), pre-release.
- **Mimari karar:** `core/db.py` + `data/jarvis.db` + migration iskeleti bu
  fazda kurulur (6.5.1 onun üstüne biner); embedding'ler tabloda `BLOB`,
  ayrı FAISS index dosyası YOK.

Alt adımlar:
- [x] **`core/db.py`** (YENİ, 6.5.1'in temeli) — tek bağlantı noktası,
      `data/jarvis.db`. `PRAGMA journal_mode=WAL` + `busy_timeout=5000` +
      `foreign_keys=ON`. `data/` `.gitignore`'a eklenir (kişisel veri —
      `.env`/`security.yaml` ilkesi). Yazma çevresinde modül-seviyesi `Lock`
      (Jarvis çok-thread'li; WAL eşzamanlı okuma + tek yazar).
- [x] **Migration** — `schema_version` tablosu + `migrations/NNN_*.sql`
      sıralı/idempotent uygulama (her boot'ta eksik migration'lar koşar).
      6.5 → `001_memories.sql`.
- [x] **`core/memory.py`** (YENİ) — `remember(text, metadata=None) -> None`,
      `recall(query, k=5) -> list[str]`. **Fail-soft mutlak:** her istisna
      (model yükleme / DB / embed) → `logger.warning` + no-op / `[]`; Jarvis
      hafızasız sürer. Mevcut `brain/llm.py:history` (son 12 mesaj, oturum-içi)
      DEĞİŞMEZ — bu ayrı, oturumlar-arası bir katman.
- [x] **Embedding** — lazy modül-singleton
      `SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2",
      device="cpu")`, `normalize_embeddings=True` (cosine = dot). İlk
      kullanımda ~470 MB HF cache'e iner (Whisper/XTTS deseni). CPU'da —
      **VRAM bütçesine dokunmaz** (v2 §4.4). `_embed` test için
      monkeypatch'lenir. **v2 §4.4 sapması:** `all-MiniLM-L6-v2` İngilizce-
      ağırlıklı, TR sorgularda cross-lingual eşleşme zayıf — çok-dilli kardeş
      seçildi (sema aynı, 384-boyut).
- [x] **Depolama + arama** — `memories(id, ts, text, metadata_json,
      embedding BLOB)` (`np.float32` 384-dim). `recall`: in-process
      `_matrix (N×384)` + `_texts` ilk çağrıda tablodan yüklenir,
      `remember`'da eklenir; `_matrix @ q` → `argpartition` top-k → eşik
      (`>= ~0.30`). `<10k` giriş için ms-altı; `>10k` → `faiss-cpu` escape
      hatch, **`memory.py` arayüzü değişmez**.
- [x] **Guardrail kapıları** (v2 §4.3, en yüksek riskli ekleme) —
      `remember(text)`: yazmadan önce `_OUTPUT_GUARDRAIL.run(text)`, takılırsa
      yazma. `recall(query)`: dönen her metin `_INPUT_GUARDRAIL.run(result)`,
      takılan listeden çıkar. Gerekçe: kalıcı hafızaya sızan injection her
      gelecek turda tekrar enjekte olur.
- [x] **Provenance** — `metadata.source` (`"assistant_turn"` / `"user_stated"`
      / ...). 6.5 alanı yazar; tool-çıktısı-türevi hafızayı otomatik güvenmeme
      politikasını 6.10 uygular (6.10 kabul edilen sınır (f)).
- [x] **`_handle_turn()` entegrasyonu** — 6.5 kapsamı YALNIZCA `remember()`:
      her asistan turu sonunda guardrail'den geçmiş tam yanıt
      `remember(response, {"source": "assistant_turn", "lang": lang})` ile
      yazılır (additif, düşük risk). **`recall()` wiring 6.10'a ait** (seçim
      sonrası, `memory_aware` set'ler, `role: system` değil sınırlandırılmış
      blok) — v2 §4.3'ün "dispatcher öncesi recall" yerleşimi 6.10 ile
      güncellendi.
- [x] **Bağımlılık** — `requirements.txt` += `sentence-transformers`
      (torch/transformers/scikit-learn/huggingface_hub zaten var — marjinal).
- [x] **Testler** — `tests/test_db.py` (WAL/pragma; migration idempotency;
      `schema_version` ilerlemesi); `tests/test_memory.py` (`remember`+`recall`
      round-trip sahte `_embed_fn` ile; fail-soft — DB yolu bozuk → no-op;
      guardrail kapıları; `k` sınırı; boş DB → `[]`; provenance korunuyor).

**Değişmezler:** (1) Fail-soft mutlak — hiçbir koşulda Jarvis'i çökertmez/
bloklamaz. (2) `remember` yalnızca `_OUTPUT_GUARDRAIL`'den geçmiş metni yazar;
`recall` sonuçları `_INPUT_GUARDRAIL`'den geçmeden dönmez (v2 §4.3). (3)
Embedding CPU'da — VRAM'e dokunmaz. (4) `data/` gitignore'da. (5)
`brain/llm.py:history` değişmez. (6) LLM-in-loop yok — `remember`/`recall`
deterministik.

**Kabul edilen sınırlar:** (a) Ham-cümle hafızası (fact-extraction yok) →
tekrar/gürültü birikebilir; `recall` eşiği + `k` hafifletir; dedup şart
olursa Mem0 yeniden değerlendirilir. (b) Brute-force cosine `>~10k` girişte
yavaşlar → `faiss-cpu` escape hatch. (c) Kalıcı tool-poisoning-via-memory
kalan-riski (6.10 (f) ile aynı) — `_INPUT_GUARDRAIL` regex tabanlı, tam çözüm
değil; provenance alanı + 6.10 MEDIUM+ argüman guardrail'i savunma katmanları.

#### 6.5.1 Genel Yapısal Veri Katmanı — SQLite ✅ (şema kuruldu; satır yazan kod 6.6/6.8/6.9'a ait)

**Amaç**: 6.5'in anlamsal (semantic) hafızasından ayrı olarak, kesin/yapısal
sorgular gerektiren veri için (trace log agregasyonu, görev takibi, takvim
cache'i, IoT cihaz durumu). NoSQL değil çünkü: tek makine/tek kullanıcı,
yatay ölçekleme yok — SQLite'ın JSON1 uzantısı şema-esnek veri için de
yeterli. **`core/db.py` + `data/jarvis.db` + migration iskeleti 6.5'te
kuruldu**; 6.5.1 yalnızca yeni tablolar + migration'lar ekler.

Alt adımlar:
- [x] **Merkezi modül** — `core/db.py` (6.5'te kuruldu: tek bağlantı, WAL,
      `schema_version` + `migrations/NNN_*.sql`).
- [x] **§9'un genellenmesi** — ayrı bir `trace.db` YOK; `traces` tablosu
      `data/jarvis.db`'de (`002_structural_tables.sql`). `core/trace.py`'nin
      kendisi (satır yazımı + `/trace` komutu) Faz 6.9'a ait ve bu tabloyu
      hedefler — 6.5.1 yalnızca şemayı ve "ayrı dosya kalmaz" kararını sabitler.
- [x] **İlk tablolar** — `002_structural_tables.sql` 4 tabloyu kurar:
      `traces` (v2 §9: `role/model/input_summary/duration_ms/token_count/result`
      CHECK'li), `tasks` (`source/text/status` CHECK + `detail_json`, 6.6
      pending-approval için), `calendar_cache` + `iot_devices` JSON1 iskeleti
      (`raw_json TEXT` + `json_extract` VIRTUAL türetilmiş sütunlar +
      indeksler). Testler: `tests/test_db.py` (+7: sürüm 2'ye ilerleme,
      4 tablo, idempotency `[1,2]`, CHECK kısıtları, generated-column round-trip).

### 6.6 Execution Modes — Scheduled & Continuous (v2 Faz F) ✅

Yeni girdi kaynakları HİÇBİR yeni güvenlik yolu açmaz — olaylar aynı
`_handle_turn()` → guardrail → dispatcher → `_run_tool_pipeline()` zincirinden
geçer, tek fark `InputEvent.source` alanı ve aşağıdaki risk kısıtı. 6.4'ün
manifest `risk_level` doğrulamasına bağlıdır.

Alt adımlar:
- [x] **`core/scheduler.py`** — cron-tabanlı; `InputHub`'ın `queue.Queue`'una
      `InputEvent(source="scheduled", text=<önceden tanımlı komut>)` koyar.
      `config/scheduled_tasks.yaml(.example)` (fail-loud, `security.yaml` gibi;
      alanlar `name`/`cron`/`text`).
- [x] **`core/continuous_runner.py`** — `jarvis-mic`/`jarvis-text-input`
      deseninde daemon thread; bir koşulu izler (dosya değişimi, MCP kaynağı,
      IoT sensörü) ve `InputEvent(source="continuous", ...)` üretir;
      `docs/mimari-genel-bakis.md` §19 thread haritasına eklenir, `stop_event`
      ile kapanır.
- [x] **Risk kısıtı** (v2 §5.3) — `source in {"scheduled","continuous"}` olan
      olaylar yalnızca `risk_level == RiskLevel.LOW` aracı otomatik tetikler;
      `execution_mode: scheduled|continuous` işaretli bir manifest MEDIUM+ risk
      taşıyorsa boot'ta reddedilir (`registry_loader` doğrular, `print_system`
      ile uyarır, yüklemez). Bu yollardan gelen MEDIUM+ eylem, kullanıcının
      sonradan onaylayacağı bir pending-approval kaydı oluşturur (HUD/`/status`).
      **Boot tarafı zaten Faz 6.4'te** (`registry_loader.py`). **Runtime tarafı
      bu fazda:** `_run_tool_pipeline`'da `source ∈ {scheduled,continuous}` +
      `requires_approval` → `core/pending_tasks.py:record_pending()` ile `tasks`
      tablosuna `pending` kayıt, `/status`'ta görünür. `/approve`/`/deny`
      tüketimi ertelendi.
- [x] **Delege zinciri gate kapsamı** — scheduled/continuous kaynaklı bir olay
      `delegate_complex`/`delegate_code` intent'ine sınıflanırsa, tüm delege
      zinciri reddedilir ve TEK bir pending kaydı oluşturulur (adım-başı pending
      semantiği `/approve` ile birlikte ele alınacak, bu fazın kapsamı dışında).

### 6.7 Proje Başlatma Aracı — CreateProjectTool (v2 Faz G) ⬜

6.1'in sertleştirilmiş `is_path_safe()`'ine ve 6.3'e (delegasyon) bağlı;
`Faz 4.5`'in "Alt Yüklenici" desenini yeniden kullanılabilir bir araca çevirir.

Alt adımlar:
- [ ] **`tools/project_tool.py:CreateProjectTool`** (`name="create_project"`,
      `risk_level=RiskLevel.HIGH`, param `project_name`) — `execute()`:
      `PROJECT_ROOT/jarvis_workspace/projects/<project_name>` yolunu
      sertleştirilmiş `is_path_safe(..., allow_create=True)` ile doğrula,
      klasörü oluştur, `templates/CLAUDE.md.template`'i proje adını gömerek
      kopyala, `spawn_detached(["claude","code"], cwd=...)`, kısa TTS yanıtı.
- [ ] **`tools/subprocess_utils.py:spawn_detached()`** — fire-and-forget
      `subprocess.Popen` (`communicate()`/`wait()` YOK — bir Claude Code
      oturumu dakikalarca sürer, ana thread'i bloklayamaz); Windows'ta
      `DETACHED_PROCESS` / `CREATE_NEW_PROCESS_GROUP`.
- [ ] **`templates/CLAUDE.md.template`** — yeni projeye kopyalanan scaffold.

### 6.8 MCP Genişletme — Google Drive + Home Assistant (v2 Faz H) ⬜

Bağımsız — herhangi bir zaman. "MCP yalnızca bilgi/veri erişimi, OS/fiziksel
kontrol yerel kalır" ilkesini (`docs/ARCHITECTURE.md` §9) korur.

Alt adımlar:
- [ ] **Google Drive MCP** — standart `config/mcp_servers.yaml` girdisi;
      not alma/okuma ile aynı kategori, risk `MEDIUM` (MCP araçlarına asla
      `LOW` verilmez).
- [ ] **Home Assistant — durum okuma** — Home Assistant MCP sunucusu üzerinden
      (hangi lambalar açık, sıcaklık vb.); salt bilgi.
- [ ] **Home Assistant — kontrol** — yerel `tools/iot_tool.py:HomeAssistantTool`
      (HA REST API'sini doğrudan çağırır, `TOOL_REGISTRY`'ye statik kayıtlı);
      MCP üzerinden DEĞİL — fiziksel etki = OS kontrolü kategorisi. Risk cihaz
      tipine göre: ışık/priz `MEDIUM`, kilit/güvenlik cihazı `HIGH`.

### 6.9 Gözlemlenebilirlik — Tracing (v2 Faz I) ⬜

Tamamen bağımsız. `hud_bus`'a benzer ama kalıcı.

Alt adımlar:
- [ ] **`core/trace.py`** — SQLite (`core/trace.db`); her agent/tool çağrısı
      için timestamp, rol/model adı, kırpılmış/hash'lenmiş girdi özeti (tam
      metin değil — hassas veri birikimini önlemek için), süre (ms), sonuç
      (`success`/`error`/`guardrail_blocked`/`approval_denied`), varsa token
      sayısı.
- [ ] **`/trace [n]` CLI komutu** — `core/cli_commands.py` (`/status`, `/debug`
      ailesi); son N kaydı gösterir. Amaç: çift-çağrı gecikmesinin gerçek
      etkisini ve `delegate_complex` adım sayısını ölçmek.

### 6.10 Akıllı Aksiyon Katmanı — Genel Orkestrasyon Döngüsü ⬜

Tek komut → tek araç eşlemesinin ötesine geçip birden fazla **uzman
tool-set**'i bağlama göre zincirleyen genel amaçlı orkestrasyon. **Senaryoya
özel dallanma YOK** — 6.3'ün sınırlı çok-adımlı döngüsü (`_run_delegate_complex`)
+ 6.4 Agent Registry + 6.5 `recall()` üzerine kurulu bir genelleştirme
katmanı. Faz 6'nın capstone'u.

**Kabul kriteri (architecture-reviewer daraltması):** yeni bir yetenek
eklerken **orkestrasyon ÇEKİRDEĞİNDE** (`core/dispatcher.py`,
`_run_delegate_complex` döngüsü, `_handle_turn`, seçim promptu) kod değişikliği
GEREKMEZ. Yeni bir **yaprak `Tool` sınıfı** hâlâ izole bir `tools/*.py`
modülüdür (dış yan-etki içeren araçlar MCP olamaz — 6.10.2 ilkesi) ama bunu
kaydetmek Faz 6.4 registry'siyle zaten çözülü. Test: `tests/test_orchestration.py`
fixture'ında hayali bir `email_send` tool-set'i (+ sahte `send_email` Tool
sınıfı) eklenir; döngü + seçim onu sıfır çekirdek değişikliğiyle kullanır.

**Bağımlılıklar:** sıkı → 6.3 (✅ sınırlı döngü), 6.4 (✅ registry/manifest),
6.5 (`recall()` — senaryo #2 için). Yumuşak/çapraz → 6.8 (Google Calendar MCP
okuma yolu; `config/mcp_servers.yaml` girdisi hangi fazda eklendiği net
yazılır, çift eklenmez). Konum: 6.9'dan sonra.

**Doğrulama senaryoları** (kapsam TANIMI değil, kapsam TESTİ — genel döngü
5'ini de aynı mekanizmayla, senaryoya özel dallanma olmadan çözer):

| # | Girdi | Seçilen set(ler) | Adımlar | Risk kapısı |
|---|-------|------------------|---------|-------------|
| 1 | "şunu webte araştır ve not al" | `web_research` | `web_search` (MCP/MEDIUM) → `create_note` (MEDIUM `[Y/N]`), adım-1 sonucu bağlam | her adım kendi riski |
| 2 | "biraz Dio aç" (şarkı adı yok) | `media_extended` | `recall(task)` → `search_music`(memory_context) (LOW, promptsuz) | LOW → onaysız |
| 3 | "şu videoyu YouTube'da aç" | `web_browser` | `open_url` / arama (MEDIUM, tarayıcı kontrolü) | MEDIUM → `[Y/N]` |
| 4 | "bu haftaki boş günlerimi söyle" | `calendar` | `list_events` (MCP okuma) → agent özetler (`no_tool_needed`) | okuma riski |
| 5 | "yarın 15:00'e diş hekimi ekle" | `calendar` | `create_event` (HIGH) → `_run_tool_pipeline` step 2, `preview_args()`'ın döndürdüğü ÇÖZÜLMÜŞ mutlak `{title, start_iso, end_iso, tz}` onay panelinde | HIGH → zorunlu onay |

#### 6.10.1 Genel orkestrasyon döngüsü (tek seferlik ÇEKİRDEK altyapı)

`_run_delegate_complex` (`core/app.py`) şu akışa genelleşir; adım döngüsünün
yapısı 6.3'ten korunur. `ToolSet` dataclass ve `load_toolsets()`
**`core/registry_loader.py`'de** (`load_dynamic_tools()` yanında — aynı
manifest→nesne şekli, aynı allowlist, aynı fail-soft felsefesi); seçim promptu
ve tool-set bilgisi **`core/app.py`'de** (`_TOOL_AGENT_SYSTEM_PROMPT` yanında).
**`core/dispatcher.py` tool-set kavramından habersiz kalır** (ARCHITECTURE §14).

1. Görev alımı + `_INPUT_GUARDRAIL` (mevcut).
2. **Tool-set seçimi** — tek ucuz `router` (`qwen2.5:3b`) çağrısı: `task` +
   kayıtlı tool-set AÇIKLAMALARI + `no_toolset` sentinel → 1–N set. (Hafıza
   recall'ü seçimden SONRA, adım 3 — böylece per-set `memory_aware` bayrağı
   bilinir ve seçim öncesi kör bir recall yapılmaz.) Bireysel araç şemalarına
   DEĞİL, set `description` + `trigger_hints`
   birleşimine karşı sınıflandırır. Seçim promptu **YALNIZCA** her set'in
   yükleme-zamanında taranmış `description` + `trigger_hints`'inden üretilir;
   set-özel statik metin İÇERMEZ (`trigger_hints` per-set disambiguation'ın
   tek yeri). Taranmış manifest metni `task`/memory'den açık bir delimiter'la
   ayrılır.
   · **0 set kayıtlı VEYA seçim boş/`no_toolset` VEYA seçim çağrısı hata/
     bozuk yanıt → adım atlanır, döngü MEVCUT düz `all_tools()` şemasıyla
     çalışır** (6.3 ile birebir). Bu bir güvenlik kontrolü DEĞİL, yalnızca
     bağlam daraltması — fail-open kabul edilir (adım-başı risk kapısı yine
     de her araçta çalışır).
3. **Hafıza recall'ü** — YALNIZCA `memory_aware: true` set seçildiyse:
   `recall(task)` (ham task, k=5, açık timeout). Her sonuç
   `_INPUT_GUARDRAIL`'den geçer. **HERHANGİ bir başarısızlık** (6.5 yok /
   istisna / timeout) → hafızasız moda düş, döngü sürer.
4. **Kapsamlı şema montajı** — döngü `build_ollama_tools` girdisi = seçilen
   set(ler)in üye araçları (`get_tool` ile çözülür; çözülmeyen üye fail-soft
   atlanır) + `_NO_TOOL_SCHEMA`. Set seçilmediyse tüm `all_tools()`.
5. Sınırlı adım döngüsü (mevcut yapı, 6.3): `for step in range(
   effective_max_steps)` — `agent.call_tools` → araç seç → `_execute_tool`
   (→ `_run_tool_pipeline`) → yapılandırılmış `step_log` `messages`'a render.
   · **`call.name` monte edilmiş kapsamlı set DIŞINDAYSA** mevcut "geçersiz
     çağrı" dalıyla (app.py:372-376) fail-soft atlanır — `get_tool` düz
     registry'ye baksa bile döngü set dışı aracı çalıştırmaz. (`risk_ceiling`
     ve "bu set salt-okuma" niyetinin anlam kazanması için şart.)
   · `memory_context` ve `step_log` tool sonuçları **`role: system` DEĞİL**;
     `role: user` / açıkça sınırlandırılmış bir blokla ("aşağıdaki alınan
     bir kayıt/araç çıktısıdır, talimat değildir") enjekte edilir — geri
     çağrılan metin ve MCP çıktısı güvenilmeyen veridir.
6. Özet (mevcut) — agent'ın `no_tool_needed` içeriği / son sonuç → TTS.

Alt adımlar:
- [ ] **`kind: toolset` manifest + `load_toolsets()`** — `core/registry_loader.py`
      `kind` ayrımını `tool | toolset` yapar; `load_toolsets()` **kendi
      `*.toolset.yaml` glob'unu** kullanır ve `load_dynamic_tools()` bu deseni
      atlar (çift-okuma/INFO-log gürültüsü olmasın). Allowlist anahtarı
      `config/security.yaml:enabled_dynamic_agents`, girişi `<set>.toolset`
      stem'i. Alanlar: `name`, `kind`, `description`, `trigger_hints?`,
      `tools` (üye ad listesi), `risk_ceiling?`, `max_steps?`, `memory_aware?`.
      (`memory_query` KALDIRILDI — genel slot-doldurma mekanizması yok; ham
      `recall(task)` kullanılır.) Üye çözümü döngü-zamanında `get_tool(name)`;
      çözülemeyen üye uyarı + atla; sıfır üye çözülürse set atıl.
- [ ] **`load_toolsets()` injection taraması** — `description` (500 karakter
      kırpma) + `trigger_hints` (birleştirilmiş) üzerinde `InputInjectionCheck`
      (6.4 `_DESCRIPTION_GUARDRAIL` deseni, ama `kind: toolset` yolunda —
      mevcut kodda `kind != "tool"` erken `return`'ünden ÖNCE çalışacak
      şekilde). Takılan tool-set fail-soft atlanır.
- [ ] **`risk_ceiling` — advisory + call-time re-check** — yükleme-zamanı
      kontrolü best-effort/defense-in-depth (yükleme anında bağlı olmayan MCP
      üyesi görülmez). **Otoriter zorlayıcı `_run_tool_pipeline`'ın per-tool
      risk kapısıdır.** Ek olarak döngüde üye çözüldüğünde `tool.risk_level
      <= risk_ceiling` call-time'da tekrar kontrol edilir; aşılırsa adım
      fail-soft atlanır. Tool-set üyeleri hiçbir zaman `CRITICAL` olamaz
      (registry_loader `critical` reddi ile tutarlı). `risk_ceiling` yoksa
      varsayılan = HIGH'a kadar izin.
- [ ] **`max_steps` yükleme-zamanı doğrulaması** — pozitif tam sayı,
      `1 <= max_steps <= _MAX_DELEGATE_STEPS`; eksik/None/tip-hatası/aralık-
      dışı → **global tavana düş** (asla sınırsıza değil), uyarı logla.
      `effective_max_steps = min(_MAX_DELEGATE_STEPS, seçilen set'lerin
      doğrulanmış min `max_steps`'i)`; hiç set yoksa `= _MAX_DELEGATE_STEPS`.
      Sayaç yalnızca **araç çalıştıran** adımlar için; seçim-router + `recall()`
      çağrıları sayılmaz (ama araç çalıştırmadıkları için risk-kapısı da
      atlamazlar).
- [ ] **Tool-set seçim adımı** — yeni `_TOOLSET_SELECT_SYSTEM_PROMPT` sabiti
      `core/app.py`'de (dispatcher'da DEĞİL). Fail-open davranışı (adım 2).
- [ ] **Kapsamlı şema + döngü-içi kapsam zorlaması** (adım 4 + adım 5'in
      birinci maddesi).
- [ ] **`Tool.preview_args(params) -> dict` sözleşmesi** — `Tool` ABC'ye
      opsiyonel, onay ÖNCESİ çağrılan bir kanca (varsayılan: `params`'ı aynen
      döndür). `_run_tool_pipeline` step (2) `risky_values`'ı bundan üretir.
      Böylece bir aracın çözdüğü nihai/mutlak değerler (ör. `create_event`'in
      timezone dahil ISO tarih/saati) onay panelinde görünür. **Genel bir
      çekirdek kanca — senaryoya özel değil**, her araç uygulayabilir.
- [ ] **Hafıza bağlamı zincirleme** — adım 3 + adım 5'in ikinci maddesi
      (`role: user` / sınırlandırılmış blok).

#### 6.10.2 Uzman tool-set'ler + üye araçlar (5 senaryoyu geçirir)

"Fiziksel/dış etki = OS kontrolü kategorisi; kontrol yerel kalır, okuma MCP
olabilir" ilkesi (`docs/ARCHITECTURE.md` §9, Home Assistant §8.2 emsali).
**Per-araç davranış kısıtları paylaşılan sistem promptuna DEĞİL, aracın
`description`'ına yazılır** (`build_function_schema` zaten description'ı
şemaya dahil eder) — `_TOOL_AGENT_SYSTEM_PROMPT` her yeni set için
düzenlenmez.

- [ ] **`web_research.toolset.yaml`** — üyeler: `web_search` (mevcut MCP) +
      `create_note` (statik MEDIUM). Senaryo #1.
- [ ] **`media_extended.toolset.yaml`** — üyeler: `search_music`, `media_*`
      (mevcut statik LOW); `memory_aware: true`. Seçim sonrası `recall(task)`
      (ham task) → sanatçı tercihi bağlamı → `search_music` fuzzy/tercih-
      temelli seçer. Senaryo #2. **6.5 `recall()`'a bağımlı.**
- [ ] **`web_browser.toolset.yaml` + `tools/browser_tool.py`** — YENİ yerel
      `OpenUrlTool` (`name="open_url"`, `risk_level=RiskLevel.MEDIUM` —
      tarayıcı kontrolü); Python `webbrowser` / `os.startfile`. "Bilmediği
      URL/arama uydurmaz" kuralı aracın `description`'ında. Senaryo #3.
- [ ] **`calendar.toolset.yaml` + `tools/calendar_tool.py`** — okuma:
      `list_events` (Google Calendar MCP, salt-okuma, `config/mcp_servers.yaml`;
      6.8 ile çapraz-ref). Yazma: YENİ yerel `CreateEventTool`
      (`name="create_event"`, `risk_level=RiskLevel.HIGH`). NL→struct tarih/
      saat parse'ı `execute()` içinde; **`preview_args()`** çözülmüş mutlak
      `{title, start_iso, end_iso, tz}` döndürür → onay panelinde kullanıcı
      GERÇEK yazılacak tarih/saati doğrular. Senaryo #4, #5.
- [ ] **Genellik testi** — `tests/test_orchestration.py` fixture manifest'iyle
      hayali `email_send` tool-set'i (+ sahte `send_email` Tool); döngü +
      seçim onu sıfır çekirdek değişikliğiyle kullanır; adım-başı risk kapısı
      hâlâ ateşlenir (mock HIGH araç → mock onay istemi).

#### 6.10.3 Mutasyon yetkili delegasyon (`claude -p` yazma modu)

6.3 "bilinçli sapmalar" (2)'deki referansın karşılığı. Bugün `delegate_code`
→ `claude -p` salt-okuma; bu adım dosya DEĞİŞTİREN modu Zero-Trust onayıyla,
**sıkı hapisle** ekler. **`_run_delegate_code`'a özgü** — genel orkestrasyon
döngüsüne dokunmaz.

- [ ] **`ClaudeCodeAdapter.respond(..., writable=False)`** — per-çağrı
      argümanı, varsayılan `False`; adapter instance state'i OLARAK
      tutulmaz (yalnızca ikinci onayı alan yol `writable=True` görebilir).
      `agent_factory.py`'nin sabit-argv, `shell` yok deseni korunur.
- [ ] **İzin verilen araç kümesi — SADECE dosya düzenleme** — `writable=True`
      → `claude -p --allowedTools "Edit" "Write"` (kesin liste tasarıma
      yazılır + "neden bu minimum" gerekçesi). **`Bash` / komut çalıştırma
      açıkça YASAK.** `--dangerously-skip-permissions` **hiçbir koşulda
      kullanılmaz.**
- [ ] **Yazılabilir hedef hapsi** — writable koşu SADECE
      `PROJECT_ROOT/jarvis_workspace/<proje>/` alt ağacında (6.7
      `CreateProjectTool` hedefi). **Jarvis'in kendi deposu writable hedef
      OLAMAZ.** Açık denylist (hem `cwd` seçimi hem `--add-dir` kısıtıyla,
      her koşulda reddedilir): `.git/`, `.env`, `secrets/`, `config/`,
      `.claude/`, `agents/registry/`, `system_prompt.txt`, `src/jarvis/`.
- [ ] **İki-aşamalı onay** — `_run_delegate_code`'da `_prompt_for_approval`
      (1) mevcut HIGH "devredilsin mi", (2) `writable` isteniyorsa İKİNCİ
      açık onay: **çözülmüş mutlak yazılabilir yol + kesin CLI bayrakları +
      denylist hatırlatması** gösterilir. İkinci onay reddi → **iptal**
      (kullanıcının istemediği bir salt-okuma koşuyu sessizce yapma —
      deterministik tek davranış).
- [ ] **Sonuç özeti** — değişen dosyalar (`git diff --stat`, sabit argv,
      shell yok) TTS + HUD; ham çıktı `_OUTPUT_GUARDRAIL`'den geçer.
- [ ] **Jarvis'in kendi kodunu düzenlemesi KAPSAM DIŞI** — gerçek bir hedefse
      ayrı, sesle tetiklenemeyen, git dalı + zorunlu insan diff incelemesi
      olan bir akış olarak tanımlanır; bu delegasyon yolunun parçası değil.
- [ ] **Test** — yazma bayrağı yalnızca ikinci onaydan sonra argv'ye eklenir;
      ret → iptal; denylist yolu → reddedilir.

**Değişmezler (KAPSAM değil, GÜVENLİK sınırı — kalkmıyor):**
1. Global adım tavanı `_MAX_DELEGATE_STEPS` sabit; set yalnızca DÜŞÜRÜR
   (`min()`); eksik/bozuk `max_steps` → global tavana düşer, asla sınırsıza.
2. Her araç-çalıştıran adım `_execute_tool` (→ `_run_tool_pipeline`)'dan
   geçer — "çok-adımlı" olmak onay/guardrail/timeout'u atlamaz.
3. Kapsamlı şema **hem seçim daraltması hem çalıştırma hapsi**: döngü,
   monte edilmiş set dışındaki araç adını fail-soft atar.
4. Tool-set'ler `enabled_dynamic_agents` allowlist'iyle `<set>.toolset`
   stem'i üzerinden kapılı; üye dinamik araçlar ayrıca kendi stem'leriyle;
   MCP/statik üyeler kendi mekanizmalarıyla.
5. `risk_ceiling` advisory; otoriter zorlayıcı `_run_tool_pipeline` per-tool
   risk kapısıdır (+ call-time re-check). Tool-set üyeleri asla `CRITICAL`.
6. Geri çağrılan hafıza ve tool sonuçları `role: system` DEĞİL,
   sınırlandırılmış `user`/veri bloğuyla enjekte edilir. `recall()` girişte
   `_INPUT_GUARDRAIL`'den, `remember()` çıkışta `_OUTPUT_GUARDRAIL`'den geçer
   (v2 §4.3); yeni tercih-öğrenme sistemi yok.
7. MCP üyeler fail-soft, asla `LOW`.
8. Mutasyon delegasyonu: (a) yalnızca ikinci açık onaydan sonra; (b) SADECE
   `--allowedTools Edit Write` (Bash/komut yasak, skip-permissions yasak);
   (c) SADECE `jarvis_workspace/<proje>/` hapsinde, denylist zorunlu; (d)
   `writable` per-çağrı argümanı, instance state değil.
9. Mutasyon-yetkili delegasyon araçları **tool-set üyeliğine uygun DEĞİLDİR**
   — genel döngü ikinci onayı sağlayamaz; `load_toolsets` üye çözümünde
   böyle bir aracı reddeder.

**Bilinçli sapmalar / kabul edilen sınırlar:**
- (a) Seçim adımı 3. bir LLM çağrısı ekler — yalnızca `delegate_complex`
  yolunda (zaten "yavaş/karmaşık"), set açıklamalarına karşı küçük prompt,
  0 set kayıtlıyken atlanır; `/trace` ile ölçülür.
- (b) Seçim `router` modelini kullanır (`tool_agent` değil); `Dispatcher.
  classify()`'a katlanmaz — dispatcher tool-set kavramından habersiz kalır
  (katmanlama).
- (c) Küçük `router` modeli her yeni set için güvenilir tetiklenmeyi
  `trigger_hints`'e bağlar (`delegate_complex` emsali, dispatcher.py:188-192);
  set-özel few-shot çekirdek prompt'a EKLENMEZ — `trigger_hints` yükleme-
  zamanı taranmış manifest verisidir.
- (d) "Yeni yetenek = sadece manifest" tam doğru değil: yeni yaprak `Tool`
  hâlâ izole bir `tools/*.py` sınıfıdır (6.4 registry'siyle kayıtlı);
  ÇEKİRDEK (dispatcher/döngü/seçim promptu) değişmez.
- (e) `risk_ceiling` load-time kontrolü, yükleme anında bağlı olmayan MCP
  üyesi için eksik kalabilir — call-time re-check + `_run_tool_pipeline`
  bunu telafi eder.
- (f) **Kalıcı tool-poisoning-via-memory** kalan-riski: `recall()` taraması
  regex tabanlı (`InputInjectionCheck`, v2 §10 madde 11 sınırlı olduğunu
  kabul ediyor). MEDIUM+ araca hafıza-türevi argüman giderse
  `_run_tool_pipeline` step (1) `_OUTPUT_GUARDRAIL` + onay paneli savunması
  var; LOW araçlarda (senaryo #2) yok, etki düşük. Hafıza kayıtlarına köken
  (provenance) etiketi ileride değerlendirilir.
- (g) `recall()`/seçim-router çalışma-zamanı hataları fail-soft/fail-open
  ele alınır (yalnızca "modül yok" değil).

---

## Notlar

- Bu dosya `CLAUDE.md`'nin 200 satır kısıtından muaf; detay burada birikir.
  Mimari tasarımın "nasıl/neden"i için `docs/ARCHITECTURE.md`'ye bak.
- Performans/kararlılık sorunları ve bug takibi (feature değil) ayrı bir canlı
  listede: `docs/optimizasyon-plani.md` — canlı test bulguları ve session-arası
  optimizasyon backlog'u oraya eklenir.
- Her adım tamamlandığında durum etiketini (✅/🟡/⬜) güncelle, böylece
  `CLAUDE.md`'deki kısa özetle senkron kalır.
- Yeni bir adıma başlarken önce **plan mode** ile keşif yap (bkz. rehber
  `docs/claude-code-rehberi.md` §7 "Günlük döngü").
