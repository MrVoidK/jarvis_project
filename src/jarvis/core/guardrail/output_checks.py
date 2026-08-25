"""Cikti tarafi guardrail kontrolu - bir ajanin urettigi metinde tehlikeli sistem komutlarini
yakalar (OWASP LLM02 Insecure Output Handling). Faz 3'te terminal-komut-calistirma tool'u
eklendiginde, o tool'un LLM ciktisini korlemesine onaysiz guvenmemesi icin bu kontrol
onceden hazir bulunuyor (bkz. CLAUDE.md security-reviewer notu).
"""

import re

from src.jarvis.core.guardrail.base import GuardrailCheck, GuardrailResult

# Tehlikeli/yikici komut kaliplari.
#
# ONEMLI - bu bir "sanitizer" DEGIL, sadece bir ilk eleme katmani: kapsamli bir
# kara liste imkansizdir (sonsuz varyant), gercek guvence her zaman insan onayidir
# (bkz. tools/shell.py, katman 1). Bu listenin amaci, kullanicinin dikkatsizce "Y"ye
# basma ihtimalinin en yikici kaliplar icin hic dogmamasi.
#
# `_IGNORECASE` HEPSINE uygulanir: bir guvenlik incelemesinde (security-reviewer,
# Faz 3) `rm -rf` ve `mkfs` kaliplarinin IGNORECASE'siz oldugu, dolayisiyla
# "RM -RF C:\..." yazimiyla sessizce atlatilabildikleri bulundu - Whisper
# transkripsiyonu buyuk/kucuk harf tutarliligi garanti etmedigi icin bu, kotu
# niyet olmadan bile tetiklenebilecek gercek bir aciklikti.
_DANGEROUS_PATTERNS = [
    # POSIX yikici kaliplar (bayrak sirasi/ayrimi degisebilir: -rf, -fr, -r -f)
    re.compile(r"\brm\s+(-\w+\s+)*-[rf]{1,2}\b", re.IGNORECASE),
    re.compile(r"\bmkfs(\.\w+)?\b", re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", re.IGNORECASE),  # fork bomb
    # Windows cmd yikici kaliplar (bayrak sirasi serbest: /s /q, /q /s, /f /s...)
    re.compile(r"\b(del|rd|rmdir)\s+(/\w+\s+)*/[fsq]\b", re.IGNORECASE),
    re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE),
    re.compile(r"\bdiskpart\b", re.IGNORECASE),
    re.compile(r"\bcipher\s+/w\b", re.IGNORECASE),
    re.compile(r"\breg\s+delete\b", re.IGNORECASE),
    re.compile(r"\btakeown\b|\bicacls\b", re.IGNORECASE),  # yetki/sahiplik degistirme
    # Kapatma/yeniden baslatma (hem cmd hem PowerShell)
    re.compile(r"\bshutdown\b|\b(stop|restart)-computer\b", re.IGNORECASE),
    # PowerShell yikici kaliplar.
    # NOT: bayrak oncesinde `\b` KULLANILMAZ - bosluk ve `-` ikisi de kelime-disi
    # karakter oldugundan aralarinda kelime siniri yoktur, `\b-recurse` hicbir zaman
    # eslesmez (bu tam olarak ilk yazimda yapilan ve regresyon testinin yakaladigi hata).
    re.compile(r"\bremove-item\b.*-recurse\b", re.IGNORECASE),
    re.compile(r"\bremove-item\b.*-force\b", re.IGNORECASE),
    # Uzaktan kod calistirma / veri sizdirma zincirleri (LOLBAS)
    re.compile(r"-enc(odedcommand)?\b", re.IGNORECASE),  # base64 gizlenmis PowerShell
    re.compile(r"\biex\b|\binvoke-expression\b", re.IGNORECASE),
    re.compile(r"\|\s*(bash|sh|iex|powershell)\b", re.IGNORECASE),  # curl ... | sh
    re.compile(r"\bcertutil\b.*-urlcache\b", re.IGNORECASE),
    # `advfirewall` da eslesmeli - bu yuzden \b ile "firewall" kelimesini degil,
    # icinde firewall GECEN her netsh alt komutunu ariyoruz.
    re.compile(r"\bnetsh\b.*firewall", re.IGNORECASE),
    # Veritabani
    re.compile(r"\bdrop\s+(table|database)\b", re.IGNORECASE),
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
