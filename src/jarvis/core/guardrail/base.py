"""AI Guardrail katmani - Chain of Responsibility deseni (bkz. docs/ARCHITECTURE.md SS6).

Her GuardrailCheck bagimsiz, tek bir seyi kontrol eder; GuardrailChain bunlari sirayla
calistirip ilk reddeden kontrolde durur. Boylece yeni bir kontrol eklemek (ornegin Faz 3'te
ses biyometrisi dogrulamasi) mevcut kontrollere hic dokunmadan zincire bir eleman eklemekten
ibarettir - Chain of Responsibility'nin SOLID'deki Open/Closed prensibiyle ortusen tarafi.
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.jarvis.core.console import print_guardrail

logger = logging.getLogger("jarvis.guardrail.timing")


@dataclass
class GuardrailResult:
    """Bir GuardrailCheck'in karari - kabul/red + nedeni + hangi kontrolun karar verdigi."""

    allowed: bool
    reason: str
    check_name: str


class GuardrailCheck(ABC):
    """Zincirdeki tek bir kontrol - girdi (kullanici transkripti) veya cikti (ajan yaniti)
    metnini inceleyip bir GuardrailResult dondurur.
    """

    name: str

    @abstractmethod
    def check(self, text: str) -> GuardrailResult:
        raise NotImplementedError


class GuardrailChain:
    """Bir liste GuardrailCheck'i sirayla calistirir; ilk red'de durur (kalan kontroller
    calismaz - orn. tehlikeli bir komut zaten C1'de yakalandiysa C2'yi calistirmaya gerek yok).
    Her karar (kabul da dahil), nedeniyle birlikte loglanir.
    """

    def __init__(self, checks: list[GuardrailCheck]) -> None:
        self._checks = checks

    def run(self, text: str) -> GuardrailResult:
        for check in self._checks:
            # /debug (core/cli_commands.py) icin sure olcumu - varsayilan
            # INFO seviyesinde gorunmez, sadece DEBUG'a gecildiginde.
            start = time.perf_counter()
            result = check.check(text)
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.debug("Guardrail '%s' %.2fms surdu.", result.check_name, elapsed_ms)
            print_guardrail(result.check_name, result.allowed, result.reason)
            if not result.allowed:
                return result
        return GuardrailResult(allowed=True, reason="Tum kontroller gecti.", check_name="chain")
