---
name: verify-audio-pipeline
description: Jarvis'in ses -> metin pipeline'ini calistirir ve kisa bir ornekle dogrular. Pipeline'da degisiklik yapildiktan sonra veya "pipeline'i dogrula/calistir" dendiginde kullan.
disable-model-invocation: false
---

## Pipeline'i calistir

Su an dogrudan mikrofon kaydi tetikler (dosyadan input almiyor, bkz.
`audio_handler.py:record_audio`):

```
python audio_handler.py
```

Konsolda "LISTENING" istemi geldiginde ~5 saniye konus; cikti "Jarvis Heard:
<transkript>" seklinde gelmeli.

## Dogrulama adimlari

1. Komut hatasiz sonlanmali (exit code 0), CUDA/model yukleme hatasi
   olmamali ("Initializing Jarvis systems (Ears online)..." satiri gorunmeli).
2. Transkript soylenen cumleyle mantikli olcude ortusmeli (birebir esitlik
   sart degil, whisper ciktisi kucuk farkliliklar gosterebilir).
3. Gecici `.wav` dosyasi kalmamali — Windows'ta `%TEMP%` altinda kontrol et:
   `find "$TEMP" -maxdepth 1 -name '*.wav' -newer <calistirma-oncesi-dosya>`
   (calistirmadan once `touch` ile bir zaman damgasi dosyasi olustur).
4. GPU kullaniliyorsa `nvidia-smi` ile calisma sirasinda VRAM kullanimini
   gozlemle; beklenmedik bir sekilde artmaya devam etmemeli (sizinti belirtisi).

TODO: `tests/fixtures/sample.wav` + `sample.expected.txt` eklenip
`record_audio`'ya opsiyonel bir `--input <dosya>` bayragi eklenince, bu skill
mikrofon gerektirmeyen deterministik bir testle guncellenmeli.

Sonucu ozetle: gecti/kaldi, hangi adimda basarisiz oldu, varsa duzeltme
onerisi.
