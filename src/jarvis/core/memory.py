"""Kalici semantic hafiza - oturumlar-arasi (`geçen hafta bahsettiğin proje`,
kullanici tercihleri, aktif proje baglami).

MEM0 DEGIL (bkz. docs/ROADMAP.md Faz 6.5): Mem0'in her `add()`'de fact-
extraction icin bir LLM (Ollama) turu atmasi 12 GB VRAM butcesiyle
(hermes3 + qwen + whisper + xtts) cakisirdi; ayrica `openai` bagimliligi +
hizli degisen v2.x API. Yerine DIY minimal:

  sentence-transformers (all-MiniLM-L6-v2, CPU) -> embedding
  core/db.py (data/jarvis.db) -> kalici saklama (embedding BLOB olarak)
  in-process numpy brute-force cosine -> arama

LLM-in-loop YOK - `remember`/`recall` deterministik. Ayri bir FAISS/Qdrant
index dosyasi yok (kucuk, tek-kullanicilik veri; <10k giriste brute-force
ms-alti). >10k'da `_search()` govdesi `faiss-cpu`'ya cevrilebilir, bu
modulun disa acik arayuzu (`remember`/`recall`) degismez.

FAIL-SOFT MUTLAK: her istisna (model yukleme / DB / embed) yakalanir ->
`logger.warning` + `remember` no-op / `recall` bos liste. Jarvis hafizasiz
calismaya devam eder. `brain/llm.py:history` (oturum-ici son 12 mesaj)
DEGISMEZ - bu ondan ayri, kalici bir katman.

GUARDRAIL (v2 §4.3, en yuksek riskli ekleme): kalici hafizaya sizan bir
prompt injection tek turluk degildir - her gelecek `recall()`'da context'e
yeniden girer. Bu yuzden `remember()` yazmadan ONCE `_OUTPUT_GUARDRAIL`,
`recall()` sonuclari donmeden ONCE her biri `_INPUT_GUARDRAIL`'den gecer.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from src.jarvis.core import db as db_module
from src.jarvis.core.guardrail.base import GuardrailChain
from src.jarvis.core.guardrail.input_checks import InputInjectionCheck
from src.jarvis.core.guardrail.output_checks import OutputSafetyCheck

logger = logging.getLogger("jarvis.core.memory")

# Jarvis iki dilli (TR/EN). `all-MiniLM-L6-v2` Ingilizce-agirlikli - Turkce
# sorgularda cross-lingual eslesme zayif ("biraz Dio ac" statement'a
# baglanamiyordu). Cok-dilli kardesi (ayni 384-boyut, sema degismez; ~470 MB
# CPU, ilk kullanimda iner - Whisper/XTTS deseni). v2 §4.4'un `all-MiniLM`
# onerisinden bilincli sapma (iki-dillilik gerekcesi).
_EMBED_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
_EMBED_DIM = 384
# recall() bu esigin altinda skoru olan sonuclari ELER - alakasiz bir hafiza
# context'e girip kucuk modeli yanlis yonlendirmesin. Model-bagimli sezgisel
# esik; gercek veri biriktikce ayarlanabilir (bkz. ROADMAP §6.5 kabul edilen
# sinir (a): ham-cumle hafizasi + `k` siniri + bu esik).
_RECALL_MIN_SCORE = 0.25

_OUTPUT_GUARDRAIL = GuardrailChain([OutputSafetyCheck()])
_INPUT_GUARDRAIL = GuardrailChain([InputInjectionCheck()])

_lock = threading.Lock()
_model = None  # lazy SentenceTransformer (ilk remember/recall'da yuklenir)
_matrix: Optional[np.ndarray] = None  # (N, D) float32, satirlar L2-normalize
_texts: list[str] = []
_loaded = False


def _embed(texts: list[str]) -> np.ndarray:
    """Metin(ler)i (N, D) L2-normalize float32 matrise cevirir.

    Model ilk cagrida yuklenir (CPU, ~90 MB HF cache'e iner - Whisper/XTTS
    ilk-indirme deseniyle ayni). Testler bu fonksiyonu monkeypatch'ler.
    """
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer  # lazy: boot'u yavaslatma

        _model = SentenceTransformer(_EMBED_MODEL_NAME, device="cpu")
    vecs = _model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
    return np.asarray(vecs, dtype=np.float32)


def _load() -> None:
    """`memories` tablosunu in-process matrise yukler (ilk erisimde bir kez)."""
    global _matrix, _texts, _loaded
    conn = db_module.get_connection()
    rows = conn.execute("SELECT text, embedding FROM memories ORDER BY id").fetchall()
    if rows:
        _texts = [r["text"] for r in rows]
        _matrix = np.stack(
            [np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]
        )
    else:
        _texts = []
        _matrix = np.zeros((0, _EMBED_DIM), dtype=np.float32)
    _loaded = True


def remember(text: str, metadata: Optional[dict] = None) -> None:
    """Bir metni kalici hafizaya yazar. Fail-soft: her hata yutulur.

    `metadata` icinde `source` alani onerilir (`assistant_turn` / `user_stated`):
    ileride (Faz 6.10) tool-ciktisi-turevi hafizayi otomatik guvenmemek icin.
    """
    text = (text or "").strip()
    if not text:
        return
    try:
        safety = _OUTPUT_GUARDRAIL.run(text)
        if not safety.allowed:
            logger.warning("remember: metin guardrail'e takildi, yazilmadi (%s).", safety.reason)
            return

        with _lock:
            if not _loaded:
                _load()
            vec = np.asarray(_embed([text])[0], dtype=np.float32)
            meta_json = json.dumps(dict(metadata or {}), ensure_ascii=False)
            ts = datetime.now(timezone.utc).isoformat()

            conn = db_module.get_connection()
            with db_module.write_lock():
                conn.execute(
                    "INSERT INTO memories (ts, text, metadata_json, embedding) "
                    "VALUES (?, ?, ?, ?)",
                    (ts, text, meta_json, vec.tobytes()),
                )
                conn.commit()

            global _matrix, _texts
            row = vec[None, :]
            _matrix = row if _matrix is None or _matrix.size == 0 else np.vstack([_matrix, row])
            _texts.append(text)
    except Exception as exc:  # noqa: BLE001 - fail-soft mutlak (bkz. modul docstring'i)
        logger.warning("remember basarisiz (yok sayildi): %s", exc)


def recall(query: str, k: int = 5) -> list[str]:
    """`query`'e en yakin en fazla `k` hafiza metnini dondurur (skor >= esik).

    Fail-soft: her hata -> bos liste. Donen her metin `_INPUT_GUARDRAIL`'den
    gecer; takilan sonuc listeden CIKARILIR (context'e hic girmez).
    """
    query = (query or "").strip()
    if not query or k <= 0:
        return []
    try:
        with _lock:
            if not _loaded:
                _load()
            if _matrix is None or len(_texts) == 0:
                return []
            q = np.asarray(_embed([query])[0], dtype=np.float32)
            scores = _matrix @ q  # satirlar normalize -> dot = cosine
            n = min(k, len(_texts))
            top = np.argpartition(-scores, n - 1)[:n]
            top = top[np.argsort(-scores[top])]
            candidates = [_texts[i] for i in top if scores[i] >= _RECALL_MIN_SCORE]

        # Guardrail kilit DISINDA (kilidi kisa tut, guardrail I/O yapmaz ama ilke).
        out: list[str] = []
        for txt in candidates:
            if _INPUT_GUARDRAIL.run(txt).allowed:
                out.append(txt)
            else:
                logger.warning("recall: bir sonuc input-guardrail'e takildi, cikarildi.")
        return out
    except Exception as exc:  # noqa: BLE001 - fail-soft mutlak
        logger.warning("recall basarisiz (bos liste dondu): %s", exc)
        return []


def reset_for_tests() -> None:
    """Modul-seviyesi durumu sifirlar (yalnizca testler)."""
    global _model, _matrix, _texts, _loaded
    _model = None
    _matrix = None
    _texts = []
    _loaded = False
