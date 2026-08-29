"""Tek, paylasilan dil tespiti yardimcisi.

Faz-3-oncesi bug-fix: kural tabanli yanitlar (core.dispatcher/core.handlers/core.app)
onceden Brain'in hata-mesaji desenini (iki dili tek cumlede birlestirip TEK bir XTTS
"lang" bayragiyla okutma) taklit ediyordu - ama Brain'in hata mesajlarinin aksine bu
yanitlar HER TURDA calisiyor, ve tek bir XTTS lang bayragi iki dilli bir metni asla
dogru telaffuz edemez (bir yarisi hep yanlis aksanla okunuyordu, orn. "Su an saat
01:49. It's 01:49 now." -> lang=en secilip Turkce kismi Ingilizce fonetikle okundu).
Cozum: kullanicinin girdisinin dilini tespit edip TEK dilde, dogru dilde yanit
uretmek - bu fonksiyon hem core hem mouth tarafindan kullanilan tek, paylasilan
tespit mantigi (iki ayri kopya birbirinden sapmasin diye).
"""

# XTTS'in destekledigi dil kodlari (bkz. mouth/tts.py inference_stream "language" parametresi).
SUPPORTED_LANGUAGES = {
    "en", "es", "fr", "de", "it", "pt", "pl", "tr", "ru", "nl",
    "cs", "ar", "zh-cn", "ja", "ko", "hu", "hi",
}

# Turkce'ye ozgu harfler - kisa/belirsiz metinlerde tr vs en ayrimi icin
# (langdetect bu tur girdilerde guvenilmez, bkz. detect_language).
_TR_CHARS = set("çğıöşüÇĞİÖŞÜ")


def _tr_or_en(text: str, default: str = "en") -> str:
    """Turkce'ye ozgu bir harf varsa 'tr', yoksa `default` (genelde 'en')."""
    return "tr" if any(ch in _TR_CHARS for ch in text) else default


def detect_language(text: str, default: str = "en") -> str:
    """Metnin dilini tespit eder - SONUC HER ZAMAN 'tr' veya 'en' (veya `default`).

    Jarvis fiilen iki dilli: Brain kullanicinin diliyle (tr/en) yanit verir,
    XTTS klonlanan ses sadece tr/en referanslari arasinda gecer (bkz.
    mouth/tts.py, docs/ARCHITECTURE.md SS5). 2026-08-29 (Cluster E): langdetect
    kisa/gurultulu metinlerde `es`/`fr`/`de` uretip yanlis TTS fonetigine yol
    aciyordu (canli testte "Please specify your request" -> `es`). Cozum:
    - `< 4 kelime`: langdetect'e HIC guvenme - Turkce'ye ozgu harf var mi diye bak.
    - `>= 4 kelime`: langdetect calisir; sonuc tr/en degilse ayni harf-sezgisiyle
      tr/en'e indirilir.
    KABUL EDILEN SINIR: ASCII'ye dusmus kisa Turkce ("sesi ac") en'e gider;
    gercekten Almanca konusan biri en fonetigiyle okunur - Jarvis'in kapsami disi.
    """
    stripped = text.strip()
    if not stripped:
        return default

    if len(stripped.split()) < 4:
        return _tr_or_en(stripped, default)

    from langdetect import LangDetectException, detect

    try:
        lang = detect(stripped)
    except LangDetectException:
        return _tr_or_en(stripped, default)
    if lang in ("tr", "en"):
        return lang
    return _tr_or_en(stripped, default)
