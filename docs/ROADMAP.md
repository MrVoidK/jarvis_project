# Jarvis — Detaylı Yol Haritası (alt adımlarla)

Bu dosya `CLAUDE.md`'deki kısa MVP listesinin genişletilmiş hâlidir. Her ana
adımın altında somut alt adımlar var. Durum etiketleri: ✅ tamam,
🟡 kısmen/MVP var-olgunlaştırılacak, ⬜ başlanmadı.

## 1. Ears — Ses/Girdi Pipeline 🟡

Mevcut: `audio_handler.py` — 5 sn sabit blok kayıt → geçici `.wav` →
faster-whisper (CUDA/float16) → metin.

Alt adımlar:
- [ ] Sabit 5 sn blok yerine VAD/sessizlik-tabanlı kayıt (konuşma bitince
      otomatik durma) — `webrtcvad` veya benzeri bir kütüphane değerlendir.
- [ ] Cümle sınırı bölünmesi sorunu: uzun cümleler blok sınırında kesiliyor
      mu, ölç ve gerekirse üst üste binen (overlapping) blok stratejisi dene.
- [ ] Sürekli dinleme modu: tek atımlık `transcribe()` yerine bir döngü/
      wake-word ("Jarvis") ile tetiklenen dinleme.
- [ ] Hata yönetimi: mikrofon bulunamadı, boş/sessiz kayıt, `sounddevice`
      istisnaları — şu an hiç try/except yok, `record_audio`/`transcribe`
      bu durumlarda sessizce patlıyor.
- [ ] CPU fallback: `device="cuda"` sabit kodlu; CUDA yoksa CPU'ya düşecek
      şekilde güncelle (CLAUDE.md'deki kural).

## 2. Brain — LLM Katmanı 🟡

Mevcut: `main.py` — `ollama.chat` ile `llama3.1`'e tek turluk (system +
user) mesaj gönderiliyor, yanıt tek seferde dönüyor.

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
