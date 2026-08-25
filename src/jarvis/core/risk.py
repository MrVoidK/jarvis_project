"""Zero-Trust risk puanlama + insan onayi (bkz. docs/ARCHITECTURE.md SS6 risk tablosu).

Her tool bir RiskLevel tasir (bkz. tools/base.py); Orta ve uzeri her eylem, calismadan
once kullanicinin acik onayindan gecer. Varsayilan HER ZAMAN RED - bos girdi, tanimadik
bir cevap veya kapali/yonlendirilmis bir stdin (EOFError) hepsi "hayir" sayilir; bir
onay isteminin kazara "evet" olarak yorumlanmasi, kacirilmis bir "evet"ten cok daha
pahalidir.
"""

import logging
from enum import Enum

logger = logging.getLogger("jarvis.risk")

_AFFIRMATIVE = {"y", "yes", "e", "evet"}


class RiskLevel(Enum):
    """docs/ARCHITECTURE.md SS6'daki risk tablosunun kod karsiligi."""

    LOW = "low"  # salt-okunur (dosya listeleme, sistem durumu) - onaysiz calisir
    MEDIUM = "medium"  # kalici degisiklik yapar (not yazma) - onay ister
    HIGH = "high"  # keyfi/yikici olabilir (terminal komutu) - onay ister
    CRITICAL = "critical"  # kok dizin/donanim - RFID fiziksel onayi gerektirir.
    # CRITICAL su an hicbir tool tarafindan KULLANILMIYOR: RFID donanimi ve
    # TrustElevation modulu henuz yok (bkz. docs/ARCHITECTURE.md SS6, Faz 3.2).
    # Yine de tanimli, cunku requires_approval() onu bugunden en az "onay gerekir"
    # tarafinda tutuyor - ileride eklenirse sessizce onaysiz calisma riski olmasin.


def requires_approval(level: RiskLevel) -> bool:
    """Orta ve uzeri her risk seviyesi insan onayi gerektirir (human-in-the-loop)."""
    return level is not RiskLevel.LOW


def request_approval(prompt: str) -> bool:
    """Terminalde bloklayici [Y/N] onayi sorar. Varsayilan: RED.

    Bloklayici `input()` bilincli bir tercih: onay, konusma dongusunun geri kalanini
    (Ears dahil) beklemeli - kullanici cevap verene kadar Jarvis baska bir sey
    yapmamali. Sesli onay yerine klavye tercih edildi cunku STT'nin yanlis-algilama
    payi guvenlik-kritik bir yolda kabul edilemez (bir "hayir"in "evet" duyulmasi).
    """
    try:
        answer = input(f"\n[ONAY GEREKLI] {prompt} [Y/N]: ").strip().lower()
    except EOFError:
        # stdin yok/kapali (or. arka planda calisan bir surec) - onay ALINAMADI,
        # dolayisiyla reddedilir.
        logger.warning("Onay istemi okunamadi (stdin yok) - reddedildi.")
        return False

    approved = answer in _AFFIRMATIVE
    logger.info("Onay istemi: %r -> %s", answer, "ONAYLANDI" if approved else "REDDEDILDI")
    return approved
