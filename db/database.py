import logging
import os
import stat

import aiosqlite
import config

logger = logging.getLogger(__name__)

_db: aiosqlite.Connection | None = None


def _lock_db_permissions() -> None:
    """Restrict the database file (and WAL/SHM siblings) to owner-only access (chmod 600)."""
    for suffix in ("", "-wal", "-shm"):
        path = config.DB_PATH + suffix
        if os.path.exists(path):
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    logger.info("Database file permissions restricted to owner-only (600)")


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(config.DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
        _lock_db_permissions()
    return _db


async def init_db() -> None:
    db = await get_db()
    statements = [
        """CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            canvas_token_encrypted TEXT NOT NULL,
            reminder_hour INTEGER DEFAULT 9,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            canvas_assignment_id INTEGER NOT NULL,
            canvas_course_id INTEGER NOT NULL,
            note_text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
        )""",
        "CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(telegram_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_notes_user_assignment ON notes(telegram_id, canvas_assignment_id)",
        """CREATE TABLE IF NOT EXISTS general_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
        )""",
        "CREATE INDEX IF NOT EXISTS idx_general_notes_user ON general_notes(telegram_id)",
        """CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            canvas_course_id INTEGER,
            text TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
        )""",
        "CREATE INDEX IF NOT EXISTS idx_todos_user ON todos(telegram_id)",
    ]
    for stmt in statements:
        await db.execute(stmt)

    # Idempotent column additions
    for col in (
        "canvas_token_ciphertext_b64 TEXT",
        "canvas_token_nonce_b64 TEXT",
        "canvas_token_wrapped_dek_b64 TEXT",
        "token_source TEXT",
    ):
        try:
            await db.execute(f"ALTER TABLE users ADD COLUMN {col}")
        except Exception as e:
            if "duplicate column name" not in str(e).lower():
                raise

    await db.commit()


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None
