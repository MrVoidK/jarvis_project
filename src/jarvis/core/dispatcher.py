"""Intent yonlendirici - once rule-based (regex/anahtar kelime), eslesme yoksa LLM-tabanli
siniflandirma (hibrit) (bkz. docs/ROADMAP.md Faz 2.1).

Amac, her kullanici transkriptini bir Intent'e (isim + guven skoru + parametreler) cevirmek;
handler'lara gercek yonlendirme (core.app icine baglanma) Faz 2'nin ilerleyen bir adimi -
burada sadece siniflandirma sozlesmesi ve iskeleti var.
"""

import logging
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

from src.jarvis.adapters.agent_factory import AgentFactory

logger = logging.getLogger("jarvis.dispatcher")

IntentSource = Literal["rule", "llm"]

# Bilinmeyen/rule-eslesmeyen her sey buraya duser - Faz 2.1'de "genel sohbet" (Brain'in
# bugunku davranisi) ile es anlamli; Faz 3'te gercek tool-calling intent'leri eklendikce
# bu varsayilan giderek daha az tetiklenecek.
DEFAULT_INTENT_NAME = "chat"

# ROADMAP'teki ornek komutlarla tutarli, kucuk bir rule-based baslangic seti.
# Regex'ler kelime siniri (\b) ile TR/EN karisik konusmada yanlis eslesmeyi azaltir.
_RULES: dict[str, re.Pattern] = {
    "get_time": re.compile(r"\bsaat kaç\b|\bwhat time is it\b", re.IGNORECASE),
    "list_files": re.compile(r"\bdosya(ları)? listele\b|\blist (the )?files\b", re.IGNORECASE),
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

    def classify(self, text: str) -> Intent:
        for name, pattern in _RULES.items():
            if pattern.search(text):
                logger.info("Dispatcher: kural eslesti (%s).", name)
                return Intent(name=name, confidence=1.0, source="rule")

        logger.info("Dispatcher: kural eslesmedi, LLM siniflandirmasina dusuluyor.")
        orchestrator = AgentFactory.create("orchestrator")
        prompt = _CLASSIFY_PROMPT_TEMPLATE.format(
            intents=", ".join(self._known_intents), text=text
        )
        raw_label = orchestrator.respond(prompt).strip().lower()

        label = raw_label if raw_label in self._known_intents else DEFAULT_INTENT_NAME
        confidence = 0.6 if raw_label in self._known_intents else 0.3
        logger.info("Dispatcher: LLM etiketi=%r -> intent=%r", raw_label, label)
        return Intent(name=label, confidence=confidence, source="llm")
