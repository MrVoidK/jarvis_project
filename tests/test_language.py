"""core/language.py:detect_language testleri.

2026-08-29 Cluster E: langdetect kısa/gürültülü metinlerde `es`/`fr`/`de`
üretiyordu (canlı testte "Please specify your request" → `dil=es` → yanlış TTS
fonetiği). Jarvis fiilen iki dilli (tr/en) - tespit çıktısı buna daraltılır.
"""

from src.jarvis.core.language import detect_language


def test_short_turkish_by_diacritic():
    assert detect_language("sesi kıs") == "tr"
    assert detect_language("şarkıyı geç") == "tr"
    assert detect_language("günaydın") == "tr"


def test_short_without_diacritic_defaults_to_en():
    assert detect_language("volume up") == "en"
    assert detect_language("play music") == "en"
    # ASCII'ye düşmüş Türkçe kısa komut ayırt edilemez -> en (kabul edilen sınır)
    assert detect_language("sesi ac") == "en"


def test_short_spurious_language_is_clamped():
    # canlı test: langdetect bunu 'es' dedi
    assert detect_language("Please specify your request") == "en"


def test_long_turkish_paragraph_detected_as_tr():
    text = "Bugün hava çok güzel görünüyor ve biraz sonra dışarı çıkıp yürüyüş yapmak istiyorum"
    assert detect_language(text) == "tr"


def test_long_english_paragraph_detected_as_en():
    text = "The weather looks really nice today and I would like to go outside for a long walk soon"
    assert detect_language(text) == "en"


def test_long_non_tr_en_language_clamped_to_en_without_diacritic():
    # Uzun Fransizca -> tr/en degil -> diakritik yoksa en'e sabitlenir
    text = "je voudrais aller au marche cet apres midi pour acheter des fruits et du pain frais"
    assert detect_language(text) == "en"


def test_empty_returns_default():
    assert detect_language("") == "en"
    assert detect_language("   ", default="tr") == "tr"
