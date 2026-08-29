"""Çağrı izleme (tracing) - `data/jarvis.db:traces` tablosuna KALICI kayıt.

`hud_bus`'a benzer ama kalıcı (v2 §9). Her agent/tool çağrısı için bir satır:
timestamp, rol, model, KIRPILMIŞ girdi özeti (tam metin ASLA - hassas veri
birikimini önlemek için), süre (ms), sonuç, varsa token sayısı.

Amaç: çift LLM çağrısı gecikmesinin (`docs/mimari-genel-bakis.md` §20 madde 1)
gerçek etkisini, `delegate_complex` adım sayısını ve hangi rolün ne kadar
zaman/token harcadığını ölçmek. `/trace [n]` (core/cli_commands.py) son N kaydı
gösterir.

FAIL-SOFT MUTLAK (core/pending_tasks.py / core/memory.py deseni): her DB istisnası
→ `logger.warning` + güvenli varsayılan (`record_trace` no-op, `list_traces` []).
Bir izleme kaydının tutulamaması JARVIS'in ana döngüsünü ASLA etkilemez -
tracing tamamen gözlem amaçlı, davranışa dokunmaz.
"""

import logging
import time
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Optional

from src.jarvis.core import db as db_module

logger = logging.getLogger("jarvis.core.trace")

# `traces.result` CHECK'i (migrations/002_structural_tables.sql) ile aynı.
_VALID_RESULTS = frozenset({"success", "error", "guardrail_blocked", "approval_denied"})

# Girdi özeti bu uzunlukta kırpılır - tam metin ASLA yazılmaz (v2 §9: hassas
# veri birikimini önle). Hash yerine kırpma: `/trace` çıktısı insan tarafından
# okunabilir kalsın.
_SUMMARY_CHAR_LIMIT = 100


def _summarize(text: Optional[str]) -> Optional[str]:
    """Girdi metnini `_SUMMARY_CHAR_LIMIT`'e kırpar (uzunsa sonuna '…'). `None` → `None`."""
    if text is None:
        return None
    text = str(text).strip()
    if len(text) <= _SUMMARY_CHAR_LIMIT:
        return text
    return text[:_SUMMARY_CHAR_LIMIT] + "…"


def record_trace(
    role: str,
    *,
    model: Optional[str] = None,
    input_summary: Optional[str] = None,
    duration_ms: Optional[int] = None,
    token_count: Optional[int] = None,
    result: str = "success",
    db_path: Optional[str] = None,
) -> None:
    """`traces` tablosuna bir satır yazar. Her istisna yutulur (fail-soft).

    `input_summary` `_summarize()` ile kırpılır. `result` CHECK dışı bir değerse
    (`_VALID_RESULTS`) SQLite IntegrityError fırlatır - o da burada yakalanıp
    yok sayılır (satır yazılmaz ama JARVIS çökmez).
    """
    try:
        ts = datetime.now(timezone.utc).isoformat()
        conn = db_module.get_connection(db_path=db_path)
        with db_module.write_lock():
            conn.execute(
                "INSERT INTO traces (ts, role, model, input_summary, duration_ms, "
                "token_count, result) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    str(role),
                    model,
                    _summarize(input_summary),
                    int(duration_ms) if duration_ms is not None else None,
                    int(token_count) if token_count is not None else None,
                    result,
                ),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - fail-soft mutlak (bkz. modül docstring'i)
        logger.warning("record_trace başarısız (yok sayıldı): %s", exc)


def list_traces(limit: int = 20, db_path: Optional[str] = None) -> list[dict]:
    """Son `limit` iz kaydı, en yeni önce. Hata → []."""
    try:
        conn = db_module.get_connection(db_path=db_path)
        rows = conn.execute(
            "SELECT id, ts, role, model, input_summary, duration_ms, token_count, result "
            "FROM traces ORDER BY id DESC LIMIT ?",
            (max(int(limit), 0),),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001 - fail-soft
        logger.warning("list_traces başarısız (boş liste döndü): %s", exc)
        return []


class traced(AbstractContextManager):
    """Bir kod bloğunu süreleyip çıkışta `record_trace()` çağıran context manager.

    Blok içinde istisna çıkarsa `result="error"` kaydedilir ve istisna PROPAGATE
    edilir (yutulmaz - sadece kaydedilir). Çağıran, blok içinde `t.result` /
    `t.token_count` alanlarını override edebilir (örn. onay reddi →
    `t.result = "approval_denied"`).

    Kullanım:
        with traced("router", model="qwen2.5:3b", input_summary=text):
            ...
    """

    def __init__(
        self,
        role: str,
        *,
        model: Optional[str] = None,
        input_summary: Optional[str] = None,
        db_path: Optional[str] = None,
    ) -> None:
        self.role = role
        self.model = model
        self.input_summary = input_summary
        self.token_count: Optional[int] = None
        self.result = "success"
        self._db_path = db_path
        self._start = 0.0

    def __enter__(self) -> "traced":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.result = "error"
        record_trace(
            self.role,
            model=self.model,
            input_summary=self.input_summary,
            duration_ms=round((time.perf_counter() - self._start) * 1000),
            token_count=self.token_count,
            result=self.result,
            db_path=self._db_path,
        )
        return False  # istisnayı bastırma
