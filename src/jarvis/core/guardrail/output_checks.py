"""Cikti tarafi guardrail kontrolu - bir ajanin urettigi metinde tehlikeli sistem komutlarini
yakalar (OWASP LLM02 Insecure Output Handling). Faz 3'te terminal-komut-calistirma tool'u
eklendiginde, o tool'un LLM ciktisini korlemesine onaysiz guvenmemesi icin bu kontrol
onceden hazir bulunuyor (bkz. CLAUDE.md security-reviewer notu).
"""

import re

from src.jarvis.core.guardrail.base import GuardrailCheck, GuardrailResult

# Tehlikeli/yikici komut kaliplari - kapsamli bir sanitizer degil, bilinen en yikici
# kaliplari (dosya sistemi silme, disk formatlama, fork bomb, veritabani DROP, kapatma)
# yakalayan bir ilk savunma hatti.
_DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s+-rf\b"),
    re.compile(r"\bdel\s+/[fs]\b", re.IGNORECASE),
    re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),  # klasik fork bomb
    re.compile(r"\bdrop\s+table\b", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\bmkfs(\.\w+)?\b"),
]


class OutputSafetyCheck(GuardrailCheck):
    """Ajanin urettigi ciktida bilinen tehlikeli komut kaliplarini arar."""

    name = "output_safety"

    def check(self, text: str) -> GuardrailResult:
        for pattern in _DANGEROUS_PATTERNS:
            match = pattern.search(text)
            if match:
                return GuardrailResult(
                    allowed=False,
                    reason=f"Tehlikeli komut kalibi tespit edildi: {match.group(0)!r}",
                    check_name=self.name,
                )
        return GuardrailResult(
            allowed=True, reason="Bilinen tehlikeli komut kalibi bulunamadi.", check_name=self.name
        )
