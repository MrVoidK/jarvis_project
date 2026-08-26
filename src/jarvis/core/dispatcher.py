"""Intent yonlendirici - once rule-based (regex/anahtar kelime), eslesme yoksa LLM-tabanli
siniflandirma (hibrit) (bkz. docs/ROADMAP.md Faz 2.1).

Amac, her kullanici transkriptini bir Intent'e (isim + guven skoru + parametreler) cevirmek;
gercek intent->fonksiyon eslemesi core.handlers'ta, canli dongudeki kullanimi core.app'te.
"""

import logging
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

from src.jarvis.adapters.agent_factory import AgentFactory
from src.jarvis.core.console import status_spinner

logger = logging.getLogger("jarvis.dispatcher")

IntentSource = Literal["rule", "llm"]

# Bilinmeyen/rule-eslesmeyen her sey buraya duser - Faz 2.1'de "genel sohbet" (Brain'in
# bugunku davranisi) ile es anlamli; Faz 3'te gercek tool-calling intent'leri eklendikce
# bu varsayilan giderek daha az tetiklenecek.
DEFAULT_INTENT_NAME = "chat"

# ROADMAP'teki ornek komutlarla tutarli, kucuk bir rule-based baslangic seti.
# Regex'ler kelime siniri (\b) ile TR/EN karisik konusmada yanlis eslesmeyi azaltir.
#
# Her intent icin TEK bir birlesik (TR|EN) pattern yerine, dile gore AYRI pattern'ler
# tutuluyor: hangi alternatifin eslestigi, o kalibin dilini KESIN olarak veriyor. Bu,
# eslesen metnin dilini ayrica langdetect ile tahmin etmekten (bkz. core/language.py)
# cok daha guvenilir - langdetect kisa metinlerde (orn. "saat kaç?") yanlis sonuc
# verebiliyor (gercek testte TR sorgusu yanlislikla "en" olarak tespit edildi, TTS
# "It's 02:03 now." diye Ingilizce okudu) ama regex'in KENDISI zaten hangi dilde
# yazildigini biliyor - o bilgiyi bosa harcamamak gerekiyor.
# Icerik gerektiren kaliplar (?P<content>...) named group'u kullanir - match_rule()
# bunu Intent.parameters["content"]'e koyar, tool'lar oradan okur (bkz. tools/base.py).
_RULES: dict[str, list[tuple[str, re.Pattern]]] = {
    "get_time": [
        ("tr", re.compile(r"\bsaat kaç\b", re.IGNORECASE)),
        ("en", re.compile(r"\bwhat time is it\b", re.IGNORECASE)),
    ],
    "list_files": [
        ("tr", re.compile(r"\bdosya(ları)? listele\b", re.IGNORECASE)),
        ("en", re.compile(r"\blist (the )?files\b", re.IGNORECASE)),
    ],
    "create_note": [
        ("tr", re.compile(r"\bnot (?:tut|al)\b[:,]?\s*(?P<content>.+)", re.IGNORECASE)),
        ("en", re.compile(r"\btake a note\b[:,]?\s*(?P<content>.+)", re.IGNORECASE)),
    ],
    "read_notes": [
        ("tr", re.compile(r"\bnotlar[ıi]m[ıi]?\s*oku\b", re.IGNORECASE)),
        ("en", re.compile(r"\bread (?:my )?notes\b", re.IGNORECASE)),
    ],
    "run_command": [
        ("tr", re.compile(r"\bkomut çalıştır\b[:,]?\s*(?P<content>.+)", re.IGNORECASE)),
        ("en", re.compile(r"\brun command\b[:,]?\s*(?P<content>.+)", re.IGNORECASE)),
    ],
    "get_system_info": [
        ("tr", re.compile(r"\bsistem durumu\b", re.IGNORECASE)),
        ("en", re.compile(r"\bsystem status\b", re.IGNORECASE)),
    ],
    # Muzik kaliplari, gercek kullanim testinde iki dogal deneme de ("Sarki calin.",
    # "Play the Should I Stay or Should I Go via Spotify?") ilk (cok dar) haliyle
    # ESLESMEDI - ikisi de Brain'e dustu ve LLM sarkiyi CALMADIGI halde "caliniyor"
    # diye halusinasyon gordu (bkz. system_prompt.txt madde 5, ayni turda eklendi).
    # Bu yuzden fiil coklu-cekim (calin/calalim vb.) ve "the"/"via spotify" gibi
    # dolgu kelimeler kapsanacak sekilde genisletildi; content artik OPSIYONEL
    # (`.*`) - PlayMusicTool bos sorguda zaten "hangi sarkiyi?" diye soruyor,
    # bu da kullanicinin sarki adi soylemeden sadece "muzik ac" demesini
    # duzgun karsiliyor.
    "play_music": [
        ("tr", re.compile(r"\b(?:şarkı\s+)?çal(?:ın|ınız)?\b[:,]?\s*(?P<content>.*)", re.IGNORECASE)),
        # "the"/"song" gibi dolgu kelimelerini burada regex'le ayiklamaya calismak
        # ("play song: X" vs "play the song X" gibi kombinasyonlarda) sirali
        # optional group'larla kirilgan oluyordu - butun kuyruk yakalanip filtreleme
        # tools/spotify.py:_clean_query()'ye birakildi (tek yerde, test edilebilir).
        ("en", re.compile(r"\bplay\b[:,]?\s*(?P<content>.*)", re.IGNORECASE)),
    ],
    "pause_music": [
        ("tr", re.compile(r"\b(?:müziği\s+)?duraklat(?:ın|ınız)?\b", re.IGNORECASE)),
        # Regex "pause music"/"pause"i tek basina zaten dogru eslestiriyor (bkz.
        # docs/TODO.md madde 2 - gercek test testte eslesmedigi zannedilen durum
        # aslinda Whisper'in "pause"u "pass" diye yanlis transkribe etmesiydi, regex
        # DEGIL). "pass" ise cok yaygin bir kelime oldugundan tek basina eklenmedi
        # (yanlis pozitif riski yuksek) - sadece acikca "music"/"song" ile birlikte
        # gecerse STT-toleransli bir varyant olarak kabul ediliyor.
        (
            "en",
            re.compile(
                r"\bpause\b\s*(?:the\s+)?(?:music|song)?\b"
                r"|\bpass\b\s+(?:the\s+)?(?:music|song)\b",
                re.IGNORECASE,
            ),
        ),
    ],
    "skip_track": [
        ("tr", re.compile(r"\b(?:şarkıyı\s+)?geç(?:in|iniz)?\b", re.IGNORECASE)),
        ("en", re.compile(r"\bskip\b\s*(?:the\s+)?(?:song|track)?\b", re.IGNORECASE)),
    ],
}

_CLASSIFY_PROMPT_TEMPLATE = (
    "Classify the following user message into exactly one of these intents: {intents}. "
    "Reply with ONLY the intent name, nothing else.\n\nMessage: {text}"
)


class Intent(BaseModel):
    """Bir kullanici transkriptinin siniflandirma sonucu."""

    name: str
    confidence: float = Field(ge=0.0, le=1.0)
    parameters: dict = Field(default_factory=dict)
    source: IntentSource


class Dispatcher:
    """Hibrit intent siniflandirici: once `_RULES`'a bakar, eslesme yoksa Orkestrator'e
    (AgentFactory.create("orchestrator")) sinif etiketi sordurur.
    """

    def __init__(self, known_intents: Optional[list[str]] = None) -> None:
        # LLM'e siniflandirma icin gosterilecek bilinen intent listesi (rule'lardaki
        # isimler + varsayilan "chat") - Faz 3'te tool intent'leri eklendikce buyur.
        self._known_intents = known_intents or [*_RULES.keys(), DEFAULT_INTENT_NAME]

    def match_rule(self, text: str) -> Optional[Intent]:
        """Sadece `_RULES`'a bakar, LLM'e HIC gitmez - eslesme yoksa None doner.

        core.app'in canli dongusu bunu kullanir: her turda "chat" mi yoksa
        bilinen bir komut mu diye anlamak icin ayrica bir LLM cagrisi yapmak
        (classify()'in yaptigi gibi), su an sadece get_time/list_files gibi
        onemsiz kurallar varken normal sohbetin gecikmesini gereksiz yere
        ikiye katlardi (bkz. docs/ROADMAP.md Faz 2 notu).

        `parameters["lang"]`'a, eslesen pattern'in KENDI dili konur (bkz.
        `_RULES`'un ustundeki not) - langdetect'e degil, hangi dil-alternatifinin
        eslestigine guveniliyor. Handler'lar (core/handlers.py) Brain'i hic
        devreye sokmadan cevap uretiyor, yani SYSTEM_PROMPT'un "kullanicinin
        diliyle yanit ver" kuralindan faydalanamiyorlar - bu parametre olmadan
        eski cift-dilli sablon (bkz. eski _handle_get_time) TEK bir XTTS "lang"
        bayragiyla okunuyordu, metnin yarisi hep yanlis fonetikle cikiyordu.
        """
        for name, variants in _RULES.items():
            for lang, pattern in variants:
                match = pattern.search(text)
                if match:
                    logger.info("Dispatcher: kural eslesti (%s, dil=%s).", name, lang)
                    parameters: dict = {"lang": lang}
                    content = match.groupdict().get("content")
                    if content:
                        parameters["content"] = content.strip()
                    return Intent(
                        name=name, confidence=1.0, source="rule", parameters=parameters
                    )
        return None

    def classify(self, text: str) -> Intent:
        rule_match = self.match_rule(text)
        if rule_match is not None:
            return rule_match

        logger.info("Dispatcher: kural eslesmedi, LLM siniflandirmasina dusuluyor.")
        orchestrator = AgentFactory.create("orchestrator")
        prompt = _CLASSIFY_PROMPT_TEMPLATE.format(
            intents=", ".join(self._known_intents), text=text
        )
        with status_spinner("Sınıflandırılıyor..."):
            raw_label = orchestrator.respond(prompt).strip().lower()

        label = raw_label if raw_label in self._known_intents else DEFAULT_INTENT_NAME
        confidence = 0.6 if raw_label in self._known_intents else 0.3
        logger.info("Dispatcher: LLM etiketi=%r -> intent=%r", raw_label, label)
        return Intent(name=label, confidence=confidence, source="llm")
