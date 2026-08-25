"""Girdi tarafi guardrail kontrolu - OWASP LLM01 (Prompt Injection) icin ilk savunma hatti."""

import re

from src.jarvis.core.guardrail.base import GuardrailCheck, GuardrailResult

# Bilinen prompt-injection/jailbreak kaliplari (TR+EN) - regex tabanli, basit ama sifir
# bagimlilikli bir ilk savunma katmani. Kapsamli degil (OWASP LLM01 tam cozumu bir
# siniflandirma modeli gerektirir), ama en yaygin "onceki talimatlari yok say" tarzi
# saldirilari duz metin seviyesinde yakalar.
_INJECTION_PATTERNS = [
    re.compile(r"ignore (all |any )?(previous|prior|above) instructions", re.IGNORECASE),
    re.compile(r"disregard (the |your )?(system prompt|previous instructions)", re.IGNORECASE),
    re.compile(r"you are now (a|an) ", re.IGNORECASE),
    re.compile(r"forget (everything|all) (you|i) (said|told you)", re.IGNORECASE),
    re.compile(r"reveal (your |the )?system prompt", re.IGNORECASE),
    re.compile(r"\bDAN mode\b", re.IGNORECASE),
    re.compile(r"önceki (talimatları|talimatlari|komutları|komutlari) (yok say|unut)", re.IGNORECASE),
    re.compile(r"sistem promptunu (görmezden gel|gormezden gel|göster|goster)", re.IGNORECASE),
    re.compile(r"artık (sen|siz) .*sın\b", re.IGNORECASE),
]


class InputInjectionCheck(GuardrailCheck):
    """Kullanici girdisinde (transkript) bilinen prompt-injection kaliplarini arar."""

    name = "input_injection"

    def check(self, text: str) -> GuardrailResult:
        for pattern in _INJECTION_PATTERNS:
            match = pattern.search(text)
            if match:
                return GuardrailResult(
                    allowed=False,
                    reason=f"Olasi prompt injection kalibi tespit edildi: {match.group(0)!r}",
                    check_name=self.name,
                )
        return GuardrailResult(
            allowed=True, reason="Bilinen injection kalibi bulunamadi.", check_name=self.name
        )
