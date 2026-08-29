"""Zero-Trust risk puanlama + insan onayi (bkz. docs/ARCHITECTURE.md SS6 risk tablosu).

Her tool bir RiskLevel tasir (bkz. tools/base.py); Orta ve uzeri her eylem, calismadan
once kullanicinin acik onayindan gecer. Varsayilan HER ZAMAN RED - bos girdi, tanimadik
bir cevap veya kapali/yonlendirilmis bir stdin (EOFError) hepsi "hayir" sayilir; bir
onay isteminin kazara "evet" olarak yorumlanmasi, kacirilmis bir "evet"ten cok daha
pahalidir.
"""

import logging
from enum import Enum

from src.jarvis.core.console import console

logger = logging.getLogger("jarvis.risk")

_AFFIRMATIVE = {"y", "yes", "e", "evet"}
_NEGATIVE = {"n", "no", "h", "hayir", "hayır", "hayýr"}


def looks_like_approval_answer(text: str) -> bool:
    """Metin AÇIKÇA bir evet/hayır cevabı mı ('y', 'evet', 'n', 'hayır'...)?

    `input_hub.wait_for_text_answer()` bunu, onay paneli çizilmeden hemen önce
    (sesli 'onayınızı bekliyorum' anonsu sürerken) yazılmış bir `y`/`n`'yi
    cevap olarak saymak için kullanır - alakasız bir cümle/paragraf ise SAYMAZ.
    """
    norm = text.strip().lower()
    return norm in _AFFIRMATIVE or norm in _NEGATIVE


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
        # Baslik satiri ayri basiliyor (SABIT metin, markup guvenli); asil `prompt`
        # (tool icerigi tasiyabilir, orn. run_command'in komut metni) `markup=False`
        # ile okutuluyor ki icindeki `[`/`]` gibi karakterler rich tarafindan stil
        # etiketi sanilip davranis degismesin - builtin input() ile BIREBIR ayni
        # okuma/EOFError sozlesmesi korunuyor, sadece gorunum degisiyor.
        console.print("\n[bold yellow][!] ONAY GEREKLI[/bold yellow]")
        answer = console.input(f"{prompt} [Y/N]: ", markup=False).strip().lower()
    except EOFError:
        # stdin yok/kapali (or. arka planda calisan bir surec) - onay ALINAMADI,
        # dolayisiyla reddedilir.
        logger.warning("Onay istemi okunamadi (stdin yok) - reddedildi.")
        return False

    approved = answer in _AFFIRMATIVE
    logger.info("Onay istemi: %r -> %s", answer, "ONAYLANDI" if approved else "REDDEDILDI")
    return approved


def evaluate_approval_answer(answer: str) -> bool:
    """`request_approval()`nin kullandığı ayni evet/hayır kuralını, DIŞARIDAN
    zaten okunmuş bir cevap metni üzerinde uygular - stdin'i KENDİSİ okumaz.

    Hibrit girdi modunda (`core/app.py`/`core/input_hub.py`) kullanılır: onay
    bekleyen ana thread kendi `console.input()`'unu çağırmaz (bu, arka planda
    sürekli stdin okuyan metin-girdi thread'iyle YARIŞIRDI - bkz.
    input_hub.py modül docstring'i); cevap paylaşılan girdi kuyruğundan
    alınıp burada yorumlanır. Varsayılan RED ilkesi (bkz. modül docstring'i)
    burada da geçerli - `answer` boş/tanınmayan bir şeyse `False` döner.
    """
    approved = answer.strip().lower() in _AFFIRMATIVE
    logger.info(
        "Onay istemi (hibrit girdi): %r -> %s", answer, "ONAYLANDI" if approved else "REDDEDILDI"
    )
    return approved
