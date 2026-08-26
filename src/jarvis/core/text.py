"""Kucuk, paylasilan metin temizleme yardimcilari.

Sadece "komut olarak calistirilacak" ya da "arama sorgusu olarak kullanilacak"
icerikler icin kullan - notes.py gibi kullanicinin ham cumlesini oldugu gibi
saklamasi gereken yerlerde KULLANMA (bkz. docs/TODO.md madde 1: bir notun
icerigindeki noktalama silinmemeli, bu sadece "eyleme donusecek" icerik icin
gerekli).
"""

import re

_TRAILING_PUNCT_RE = re.compile(r"[.?!,;:]+$")


def strip_trailing_punct(text: str) -> str:
    """STT transkriptinin sonuna eklenen noktalama isaretini (`.`, `?` vb.) siler.

    Whisper cumle sonu noktalama ekliyor ("Run command ls." gibi) ama bu,
    komut olarak calistirilacak ya da arama sorgusu olarak kullanilacak
    icerikte istenmeyen sonuclara yol aciyor (orn. `ls.` Windows'ta
    "'ls.' is not recognized" hatasi veriyordu - bkz. docs/TODO.md madde 1).
    """
    return _TRAILING_PUNCT_RE.sub("", text).strip()
