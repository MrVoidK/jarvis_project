"""Tool - butun Faz 3 arac entegrasyonlarinin uydugu ortak sozlesme.

agents/base.py'deki Agent(ABC) deseniyle bilincli olarak simetrik: cagiran kod
(core.app) somut bir tool sinifini hic gormez, sadece bu arayuz uzerinden calisir -
yeni bir arac eklemek, mevcut cagiran kodu degistirmeden yeni bir Tool alt sinifi
yazip tools/registry.py'ye eklemekten ibarettir.

Her tool bir RiskLevel tasir; core.app calistirmadan once bu seviyeye gore
(core/risk.py:requires_approval) insan onayi ister - guvenlik karari tool'un
KENDISINE birakilmaz (bir tool'un kendi riskini "dusuk" ilan edip onaydan
kacinmasini imkansiz kilan tek merkezi kontrol noktasi).
"""

from abc import ABC, abstractmethod

from src.jarvis.core.risk import RiskLevel


class Tool(ABC):
    """Tum arac entegrasyonlarinin (not, dosya, terminal, sistem) uygulamasi gereken arayuz."""

    name: str
    description: str
    risk_level: RiskLevel

    @abstractmethod
    def execute(self, params: dict) -> str:
        """Araci calistirir ve kullaniciya SESLI okunacak tek bir cumle dondurur.

        `params`, dispatcher'in Intent.parameters'i: her zaman "lang" (tespit edilen
        dil, bkz. core/dispatcher.py) icerir; icerik gerektiren araclar ayrica
        "content" (regex named-group'undan) alir.

        Donen metin dogrudan TTS'e gidecegi icin kisa ve tek dilde olmali (params
        ["lang"] hangi dil ise) - markdown/liste/uzun cikti okunamaz.
        """
        raise NotImplementedError
