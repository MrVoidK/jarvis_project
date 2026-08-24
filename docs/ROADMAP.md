# Jarvis — Detaylı Yol Haritası (alt adımlarla)

Bu dosya `CLAUDE.md`'deki kısa MVP listesinin genişletilmiş hâlidir. Her ana
adımın altında somut alt adımlar var. Durum etiketleri: ✅ tamam,
🟡 kısmen/MVP var-olgunlaştırılacak, ⬜ başlanmadı.

## 1. Ears — Ses/Girdi Pipeline ✅ (olgunlaştırıldı, wake-word hariç)

Mevcut: `audio_handler.py` — `webrtcvad` ile VAD-tabanlı dinamik kayıt
(sabit blok yok) → ndarray (disk'e yazmadan) → faster-whisper (`turbo` =
large-v3-turbo, `language="tr"` sabit, `vad_filter=True` ile, CUDA/float16 +
otomatik CPU/int8 fallback) → metin. `listen_loop()` ile sürekli dinleme;
`main.py` bunu tüketiyor.

Alt adımlar:
- [x] Sabit 5 sn blok yerine VAD/sessizlik-tabanlı kayıt — `webrtcvad-wheels`
      ile 30ms frame'ler, ~700ms sessizlik sonrası otomatik durma, 20sn üst
      sınır.
- [x] Cümle sınırı bölünmesi sorunu — dinamik VAD kaydı sabit pencereyi
      ortadan kaldırdığı için ayrıca çözülmedi (adım 1'e "subsume" oldu);
      ek olarak `vad_filter=True` ile transkripsiyon öncesi temizlik açıldı.
- [x] Sürekli dinleme modu — `listen_loop()` generator'ı, `main.py`'de
      `for user_text in listen_loop():` ile tüketiliyor. **Wake-word
      eklenmedi** (kullanıcı kararı: önce wake-word'süz test edilecek).
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
- [x] Türkçe doğruluk — model `base` yerine `turbo` (large-v3-turbo) yapıldı,
      `transcribe()`'a `language="tr"` sabitlendi (otomatik dil algılama
      kaldırıldı, yanlış dil tespiti riski önlendi).

Gelecek/opsiyonel (kapsam dışı bırakıldı):
- [ ] Tetikleyici (wake-word ya da **çift alkış** gibi genlik/RMS tabanlı
      bir tetikleyici) — kullanıcının önceliği, wake-word'süz sürüm
      denendikten sonra değerlendirilecek. Sesli seçenek için
      `openWakeWord` (ONNX, torch gerektirmez) düşünülebilir.
- [ ] Latency profiling — `_vad_record` (yakalama) ile `model.transcribe`
      (transkripsiyon) sürelerini ayrı ayrı ölçüp loglama; CPU/int8 fallback
      modunda darboğaz muhtemelen transkripsiyon tarafında (`pipeline-debugger`
      önerisi, henüz uygulanmadı).

**İnsan doğrulaması gerekiyor:** Bu ortam headless olduğu için gerçek
konuşmayla test edilemedi — sadece sessizlik/ortam gürültüsüyle uçtan uca
smoke test yapıldı. `_vad_record`'a artık kısa bir pre-roll buffer (tetik
öncesi ~90ms) eklendi (VAD'ın tetiklenmeden hemen önceki ilk heceyi/yumuşak
sesi kırpması klasik bir sorun), ama gerçek mikrofonla — özellikle yumuşak
başlayan cümlelerle (örn. "Şey, merhaba") — doğrulanmalı.

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
