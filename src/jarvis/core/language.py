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


def detect_language(text: str, default: str = "en") -> str:
    """Metnin dilini tespit eder; bos metin veya desteklenmeyen/belirsiz sonucta `default`'a duser."""
    from langdetect import LangDetectException, detect

    if not text.strip():
        return default
    try:
        lang = detect(text)
    except LangDetectException:
        return default
    if lang.startswith("zh"):
        return "zh-cn"
    return lang if lang in SUPPORTED_LANGUAGES else default
