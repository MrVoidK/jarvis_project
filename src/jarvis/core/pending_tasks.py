"""data/jarvis.db'deki `tasks` tablosuna (Faz 6.5.1) ince erisim - scheduled/
continuous kaynakli MEDIUM+ eylemlerin "bekleyen onay" kaydi.

FAIL-SOFT (core/memory.py deseniyle ayni, BILINCLI): her DB istisnasi ->
logger.warning + guvenli varsayilan (record_pending -> None, list_pending ->
[]). Bir zamanlanmis turun kaydi tutulamazsa Jarvis'in ana dongusu ETKILENMEZ
- kayit, kullaniciya bilgi amacli; guvenlik karari zaten "calistirma"dir.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from src.jarvis.core import db as db_module

logger = logging.getLogger("jarvis.core.pending_tasks")


def record_pending(
    source: str, text: str, detail: dict, db_path: Optional[str] = None
) -> Optional[int]:
    """`tasks` tablosuna status='pending' bir satir yazar, id doner. Hata -> None."""
    try:
        ts = datetime.now(timezone.utc).isoformat()
        detail_json = json.dumps(dict(detail or {}), ensure_ascii=False)
        conn = db_module.get_connection(db_path=db_path)
        with db_module.write_lock():
            cur = conn.execute(
                "INSERT INTO tasks (ts, source, text, status, detail_json) "
                "VALUES (?, ?, ?, 'pending', ?)",
                (ts, source, text, detail_json),
            )
            conn.commit()
        return int(cur.lastrowid)
    except Exception as exc:  # noqa: BLE001 - fail-soft mutlak (bkz. modul docstring'i)
        logger.warning("record_pending basarisiz (yok sayildi): %s", exc)
        return None


def list_pending(limit: int = 10, db_path: Optional[str] = None) -> list[dict]:
    """status='pending' satirlar, en yeni once. Hata -> []."""
    try:
        conn = db_module.get_connection(db_path=db_path)
        rows = conn.execute(
            "SELECT id, ts, source, text, status, detail_json FROM tasks "
            "WHERE status = 'pending' ORDER BY id DESC LIMIT ?",
            (max(int(limit), 0),),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001 - fail-soft
        logger.warning("list_pending basarisiz (bos liste dondu): %s", exc)
        return []
