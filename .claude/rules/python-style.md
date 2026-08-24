---
paths:
  - "**/*.py"
---

# Python Kod Stili (Jarvis)

- Type hints kullan; public fonksiyon/metotlarda donus tipi de belirt.
- Fonksiyonlar tek is yapmali; bir fonksiyon hem ses yakalama hem
  transkripsiyon yapmamali — ayri fonksiyonlara bol.
- Loglama icin `print` yerine `logging` modulunu kullan.
- Bloklayici (senkron) IO ve GPU cagrilari, ana asenkron dongude
  calisiyorsa acikca isaretle (ör. yorum satiriyla neden bloklayici
  oldugunu belirt).
