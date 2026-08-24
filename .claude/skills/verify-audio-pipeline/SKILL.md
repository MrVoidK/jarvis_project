---
name: verify-audio-pipeline
description: Jarvis'in ses -> metin pipeline'ini calistirir ve kisa bir ornekle dogrular. Pipeline'da degisiklik yapildiktan sonra veya "pipeline'i dogrula/calistir" dendiginde kullan.
disable-model-invocation: false
---

## Pipeline'i calistir

VAD-tabanli, dinamik sureli kayit tetikler (sabit 5sn blok yok, bkz.
`audio_handler.py:_vad_record`):

```
python audio_handler.py
```

Konsolda "Dinleniyor..." logu geldiginde konus; ~700ms sessizlik sonrasi
kayit otomatik durur, cikti "Jarvis Heard: <transkript>" seklinde gelmeli.

## Dogrulama adimlari

1. Komut hatasiz sonlanmali (exit code 0). "faster-whisper 'cuda'/'cpu'
   cihazinda yuklendi" logu gorulmeli; hangi cihaza dustugune dikkat et —
   `_load_model_with_fallback()` CUDA hatasi durumunda otomatik CPU'ya
   duser ve bunu `[WARNING]` seviyesinde loglar.
2. Transkript soylenen cumleyle mantikli olcude ortusmeli (birebir esitlik
   sart degil, whisper ciktisi kucuk farkliliklar gosterebilir).
3. Konusmadan uzun sure sessiz kalinirsa (~20sn) "Konusma algilanmadi
   (timeout)" logu ile `None` donmeli, hata firlatmamali.
4. Gecici `.wav` dosyasi kontrolu **artik gerekmiyor** — `_vad_record()`
   kaydi dogrudan bellekte (`np.ndarray`) tutup `WhisperModel.transcribe()`'a
   disk'e hic yazmadan veriyor.
5. GPU kullaniliyorsa (`cuda` logu gorulduyse) `nvidia-smi` ile calisma
   sirasinda VRAM kullanimini gozlemle; beklenmedik sekilde artmaya devam
   etmemeli (sizinti belirtisi).

TODO: `tests/fixtures/sample.wav` + `sample.expected.txt` eklenip
`transcribe_once`'a opsiyonel bir `--input <dosya>` bayragi eklenince, bu
skill mikrofon gerektirmeyen deterministik bir testle guncellenmeli.

Sonucu ozetle: gecti/kaldi, hangi adimda basarisiz oldu, varsa duzeltme
onerisi.
