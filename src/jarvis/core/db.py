"""Merkezi SQLite - `data/jarvis.db`. Tek baglanti noktasi + sirali migration.

Faz 6.5 (kalici semantic hafiza, `core/memory.py`) burada kuruldu; Faz 6.5.1
(yapisal veri: traces, tasks, calendar_cache, iot_devices) ayni dosya + ayni
`core/db.py` uzerine biner - ayri bir `trace.db` kalmaz.

WAL modu: eszamanli okuma + TEK yazar. Jarvis cok-thread'li (mic / text-input /
tool worker'lari, bkz. core/input_hub.py) - bu yuzden `check_same_thread=False`
+ yazma cevresinde modul-seviyesi `_write_lock`. Okumalar kilitsiz (WAL bunu
guvenli kilar).

FAIL-LOUD (bilincli): `core/mcp_config.py` / `core/memory.py` fail-soft'tur
cunku opsiyonel/dis katmanlar. `db.py` ise onlarin ALTINDAKI depolama - bozuk
bir sema veya uygulanamayan migration sessizce yutulmamali. `core/memory.py`
kendi cagrilarini zaten fail-soft sarmaliyor; buradaki net hata oraya
yansir, uygulamayi cokertmez.
"""

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.jarvis.core.paths import PROJECT_ROOT

logger = logging.getLogger("jarvis.core.db")

DB_DIR = Path(PROJECT_ROOT) / "data"
DB_PATH = DB_DIR / "jarvis.db"
MIGRATIONS_DIR = Path(PROJECT_ROOT) / "migrations"

# Cross-modul yazma serilestirmesi (memory.remember + 6.5.1 trace yazmalari
# ayni dosyaya gider). `with db.write_lock():` ile kullanilir.
_write_lock = threading.Lock()

_conn: Optional[sqlite3.Connection] = None
_conn_lock = threading.Lock()


def write_lock() -> threading.Lock:
    """Tum yazma islemlerinin cevresinde tutulmasi gereken paylasimli kilit."""
    return _write_lock


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _apply_migrations(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> None:
    """`migrations/NNN_*.sql`'i sirali, idempotent uygular.

    Her boot'ta `schema_version`'daki en yuksek surumden buyuk numarali
    migration'lar calisir. Dosya adinin ilk `_`'den onceki kismi tamsayi
    surum numarasidir (`001_memories.sql` -> 1).
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    current = row["v"] or 0

    if not migrations_dir.is_dir():
        logger.warning("Migration dizini yok: %s", migrations_dir)
        return

    for path in sorted(migrations_dir.glob("*.sql")):
        try:
            version = int(path.stem.split("_", 1)[0])
        except ValueError:
            logger.warning("Migration adi 'NNN_' onekiyle baslamiyor, atlandi: %s", path.name)
            continue
        if version <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        with _write_lock:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, _now()),
            )
            conn.commit()
        logger.info("Migration uygulandi: %s (v%d)", path.name, version)


def get_connection(db_path: "os.PathLike[str] | str | None" = None) -> sqlite3.Connection:
    """`data/jarvis.db` icin paylasimli baglanti (migration'lar uygulanmis).

    `db_path` verilirse (testler) o yola AYRI, cache'lenmemis bir baglanti
    acilir ve migration'lar ona uygulanir - `core/security_config.py`nin
    `load_security_config(path=...)` deseniyle ayni.
    """
    global _conn
    if db_path is not None:
        conn = _open(Path(db_path))
        _apply_migrations(conn)
        return conn

    if _conn is None:
        with _conn_lock:
            if _conn is None:
                conn = _open(DB_PATH)
                _apply_migrations(conn)
                _conn = conn
    return _conn


def reset_for_tests() -> None:
    """Modul-seviyesi baglanti cache'ini sifirlar (yalnizca testler)."""
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except sqlite3.Error:
            pass
    _conn = None
