---
name: verify-wakeword-pipeline
description: Jarvis'in IDLE/ACTIVE state machine'ini (openWakeWord "Hey Jarvis" tetikleyicisi + VAD kaydi + transkripsiyon) uctan uca dogrular, latency loglarini kontrol eder. Wake-word/state machine kodu degistikten sonra veya "wake-word'u dogrula" dendiginde kullan.
disable-model-invocation: false
---

## Pipeline'i calistir

```
python main.py
```

`src/jarvis/ears/listener.py:listen_loop()` artik bir state machine: IDLE
(wake-word bekleniyor) <-> ACTIVE (wake-word sonrasi VAD ile kayit +
transkripsiyon).

## Doğrulama adımları

1. **IDLE davranışı:** Program açılışta "Uyku modunda... ('Hey Jarvis'
   bekleniyor)" logunu basmalı. Wake-word söylemeden **rastgele bir şey**
   konuş (ör. "bugün hava güzel") — bu konuşma **transkribe edilmemeli**,
   `[USER]:` satırı **görünmemeli**, Brain'e hiçbir şey gitmemeli. (openWakeWord
   sadece "Hey Jarvis" sınıfını tanıyor, başka konuşmaya skor vermemeli.)
2. **Wake-word tetikleme:** "Hey Jarvis" de (İngilizce telaffuza yakın
   dene). "Wake word algilandi (skor=..., bekleme=...s, ort. chunk
   gecikmesi=...ms)" logu gelmeli, skor `0.5` üzerinde olmalı.
3. **ACTIVE geçişi:** Wake-word sonrası "Dinleniyor (konusmaya
   baslayabilirsiniz)..." logu gelip normal VAD akışı (bkz.
   `verify-audio-pipeline`) devreye girmeli; konuş, `[USER]:` ve
   `[JARVIS]:` satırları görünmeli.
4. **IDLE'a dönüş:** Utterance bitince tekrar "Uyku modunda..." logu
   gelmeli — programı yeniden başlatmaya gerek olmamalı.
5. **Latency:** "Transkripsiyon gecikmesi: X.XXs" logunu kontrol et — CUDA
   modundaysa birkaç saniyeyi aşmamalı (turbo modelle). Wake-word "ort.
   chunk gecikmesi" birkaç ms mertebesinde olmalı (80ms bütçenin çok
   altında) — yüksekse (>80ms) gerçek zamanlı yetişemiyor demektir,
   `pipeline-debugger` ile araştır.
6. **Yanlış pozitif kontrolü:** Birkaç dakika normal konuşurken (wake-word
   söylemeden) hiç tetiklenmemeli. Sık yanlış pozitif oluyorsa
   `src/jarvis/ears/listener.py`'deki `WAKEWORD_THRESHOLD` (varsayılan 0.5)
   artırılabilir.
7. **Takip penceresi:** Gerçek bir yanıt sonrası açılan takip penceresi
   ("Takip penceresi acik (kalan Xs)") gürültü/sessizlikte düzgün azalıp
   ~`FOLLOWUP_WINDOW_MS` (12sn) içinde IDLE'a dönmeli — sürekli sıfırdan
   açılıp IDLE'a dönüşü süresiz ertelememeli (bkz. `docs/ROADMAP.md` Faz 1.1
   bulgusu, düzeltildi).

**Bilinen risk:** `hey_jarvis` modeli İngilizce sentetik seslerle
eğitildi — Türkçe aksanla güvenilirliği düşükse bu bir bug değil, modelin
doğal sınırı; `WAKEWORD_THRESHOLD`'u düşürüp yanlış-pozitif/yanlış-negatif
dengesini ayarlamayı dene.

Sonucu özetle: geçti/kaldı, hangi adımda başarısız oldu (IDLE'da yanlış
tetiklenme, ACTIVE'e geçmeme, IDLE'a dönmeme, yüksek gecikme), varsa
düzeltme önerisi.
