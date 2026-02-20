import logging

from cryptography.fernet import Fernet, InvalidToken
import config
from db.database import get_db

logger = logging.getLogger(__name__)

_fernet = Fernet(config.FERNET_KEY.encode())


def _encrypt(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def _decrypt(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()


# ── User CRUD ──


async def upsert_user(telegram_id: int, canvas_token: str) -> None:
    db = await get_db()
    await db.execute(
        """
        INSERT INTO users (telegram_id, canvas_token_encrypted)
        VALUES (?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET canvas_token_encrypted = excluded.canvas_token_encrypted
        """,
        (telegram_id, _encrypt(canvas_token)),
    )
    await db.commit()


async def get_canvas_token(telegram_id: int) -> str | None:
    db = await get_db()
    row = await db.execute_fetchall(
        "SELECT canvas_token_encrypted FROM users WHERE telegram_id = ?",
        (telegram_id,),
    )
    if not row:
        return None
    return _decrypt(row[0][0])


async def delete_user(telegram_id: int) -> None:
    """Delete user and all their data (token, notes, general notes, todos)."""
    db = await get_db()
    await db.execute("DELETE FROM todos WHERE telegram_id = ?", (telegram_id,))
    await db.execute("DELETE FROM general_notes WHERE telegram_id = ?", (telegram_id,))
    await db.execute("DELETE FROM notes WHERE telegram_id = ?", (telegram_id,))
    await db.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))
    await db.commit()


async def set_reminder_hour(telegram_id: int, hour: int) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE users SET reminder_hour = ? WHERE telegram_id = ?",
        (hour, telegram_id),
    )
    await db.commit()


async def get_reminder_hour(telegram_id: int) -> int | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT reminder_hour FROM users WHERE telegram_id = ?",
        (telegram_id,),
    )
    if not rows:
        return None
    return rows[0][0]


async def get_users_for_reminder_hour(hour: int) -> list[int]:
    """Return telegram_ids of users whose reminder_hour matches."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT telegram_id FROM users WHERE reminder_hour = ?",
        (hour,),
    )
    return [r[0] for r in rows]


async def is_registered(telegram_id: int) -> bool:
    db = await get_db()
    row = await db.execute_fetchall(
        "SELECT 1 FROM users WHERE telegram_id = ?", (telegram_id,)
    )
    return len(row) > 0


# ── Notes CRUD ──


async def upsert_note(
    telegram_id: int,
    canvas_assignment_id: int,
    canvas_course_id: int,
    note_text: str,
) -> None:
    db = await get_db()
    await db.execute(
        """
        INSERT INTO notes (telegram_id, canvas_assignment_id, canvas_course_id, note_text)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(telegram_id, canvas_assignment_id)
        DO UPDATE SET note_text = excluded.note_text, updated_at = CURRENT_TIMESTAMP
        """,
        (telegram_id, canvas_assignment_id, canvas_course_id, _encrypt(note_text)),
    )
    await db.commit()


async def get_note(telegram_id: int, canvas_assignment_id: int) -> str | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT note_text FROM notes WHERE telegram_id = ? AND canvas_assignment_id = ?",
        (telegram_id, canvas_assignment_id),
    )
    if not rows:
        return None
    return _decrypt(rows[0][0])


async def get_all_notes(telegram_id: int) -> list[dict]:
    db = await get_db()
    rows = await db.execute_fetchall(
        """
        SELECT canvas_assignment_id, canvas_course_id, note_text, updated_at
        FROM notes WHERE telegram_id = ? ORDER BY updated_at DESC
        """,
        (telegram_id,),
    )
    return [
        {
            "canvas_assignment_id": r[0],
            "canvas_course_id": r[1],
            "note_text": _decrypt(r[2]),
            "updated_at": r[3],
        }
        for r in rows
    ]


async def add_general_note(telegram_id: int, content: str) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO general_notes (telegram_id, content) VALUES (?, ?)",
        (telegram_id, _encrypt(content)),
    )
    await db.commit()


async def get_all_general_notes(telegram_id: int) -> list[dict]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id, content, created_at FROM general_notes WHERE telegram_id = ? ORDER BY created_at DESC",
        (telegram_id,),
    )
    return [{"id": r[0], "content": _decrypt(r[1]), "created_at": r[2]} for r in rows]


# ── Todos CRUD ──


async def add_todo(telegram_id: int, text: str, canvas_course_id: int | None = None) -> int:
    db = await get_db()
    cursor = await db.execute(
        "INSERT INTO todos (telegram_id, canvas_course_id, text) VALUES (?, ?, ?)",
        (telegram_id, canvas_course_id, _encrypt(text)),
    )
    await db.commit()
    return cursor.lastrowid


async def get_todos(telegram_id: int, include_done: bool = False) -> list[dict]:
    db = await get_db()
    if include_done:
        rows = await db.execute_fetchall(
            "SELECT id, canvas_course_id, text, done, created_at FROM todos WHERE telegram_id = ? ORDER BY done ASC, created_at DESC",
            (telegram_id,),
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT id, canvas_course_id, text, done, created_at FROM todos WHERE telegram_id = ? AND done = 0 ORDER BY created_at DESC",
            (telegram_id,),
        )
    return [
        {"id": r[0], "canvas_course_id": r[1], "text": _decrypt(r[2]), "done": r[3], "created_at": r[4]}
        for r in rows
    ]


async def toggle_todo(todo_id: int, telegram_id: int) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "UPDATE todos SET done = CASE WHEN done = 0 THEN 1 ELSE 0 END WHERE id = ? AND telegram_id = ?",
        (todo_id, telegram_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def delete_todo(todo_id: int, telegram_id: int) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM todos WHERE id = ? AND telegram_id = ?",
        (todo_id, telegram_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def get_stats() -> dict:
    """Return aggregate stats for admin dashboard."""
    db = await get_db()
    rows = await db.execute_fetchall("SELECT COUNT(*) FROM users")
    user_count = rows[0][0]
    rows = await db.execute_fetchall("SELECT COUNT(*) FROM notes")
    note_count = rows[0][0]
    rows = await db.execute_fetchall("SELECT COUNT(*) FROM general_notes")
    general_note_count = rows[0][0]
    rows = await db.execute_fetchall("SELECT COUNT(*) FROM todos")
    todo_count = rows[0][0]
    rows = await db.execute_fetchall("SELECT COUNT(*) FROM todos WHERE done = 1")
    todo_done_count = rows[0][0]
    rows = await db.execute_fetchall("SELECT COUNT(*) FROM users WHERE reminder_hour IS NOT NULL")
    reminder_count = rows[0][0]
    return {
        "users": user_count,
        "notes": note_count,
        "general_notes": general_note_count,
        "todos": todo_count,
        "todos_done": todo_done_count,
        "reminders_enabled": reminder_count,
    }


async def get_all_user_ids() -> list[int]:
    """Return all registered user telegram_ids."""
    db = await get_db()
    rows = await db.execute_fetchall("SELECT telegram_id FROM users")
    return [r[0] for r in rows]


async def search_notes(telegram_id: int, query: str) -> tuple[list[dict], list[dict]]:
    """Search assignment notes and general notes by keyword. Returns (assignment_notes, general_notes).

    Since note content is encrypted, we decrypt all user notes and filter in Python.
    """
    query_lower = query.lower()

    assignment_notes = await get_all_notes(telegram_id)
    matching_assignment = [
        n for n in assignment_notes if query_lower in n["note_text"].lower()
    ]

    general_notes = await get_all_general_notes(telegram_id)
    matching_general = [
        n for n in general_notes if query_lower in n["content"].lower()
    ]

    return matching_assignment, matching_general


async def delete_note(telegram_id: int, canvas_assignment_id: int) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM notes WHERE telegram_id = ? AND canvas_assignment_id = ?",
        (telegram_id, canvas_assignment_id),
    )
    await db.commit()
    return cursor.rowcount > 0


# ── Legacy migration ──


def _is_encrypted(value: str) -> bool:
    """Heuristic: Fernet tokens are url-safe base64 starting with 'gAAAAA'."""
    try:
        _fernet.decrypt(value.encode())
        return True
    except Exception:
        return False


async def migrate_encrypt_legacy_rows() -> dict:
    """Re-encrypt any plain-text legacy rows in-place. Safe to run multiple times.

    Returns a dict with counts of migrated rows per table.
    """
    db = await get_db()
    migrated = {"users": 0, "notes": 0, "general_notes": 0, "todos": 0}

    # ── users.canvas_token_encrypted ──
    rows = await db.execute_fetchall("SELECT telegram_id, canvas_token_encrypted FROM users")
    for r in rows:
        tid, val = r[0], r[1]
        if not _is_encrypted(val):
            await db.execute(
                "UPDATE users SET canvas_token_encrypted = ? WHERE telegram_id = ?",
                (_encrypt(val), tid),
            )
            migrated["users"] += 1

    # ── notes.note_text ──
    rows = await db.execute_fetchall("SELECT id, note_text FROM notes")
    for r in rows:
        nid, val = r[0], r[1]
        if not _is_encrypted(val):
            await db.execute(
                "UPDATE notes SET note_text = ? WHERE id = ?",
                (_encrypt(val), nid),
            )
            migrated["notes"] += 1

    # ── general_notes.content ──
    rows = await db.execute_fetchall("SELECT id, content FROM general_notes")
    for r in rows:
        nid, val = r[0], r[1]
        if not _is_encrypted(val):
            await db.execute(
                "UPDATE general_notes SET content = ? WHERE id = ?",
                (_encrypt(val), nid),
            )
            migrated["general_notes"] += 1

    # ── todos.text ──
    rows = await db.execute_fetchall("SELECT id, text FROM todos")
    for r in rows:
        nid, val = r[0], r[1]
        if not _is_encrypted(val):
            await db.execute(
                "UPDATE todos SET text = ? WHERE id = ?",
                (_encrypt(val), nid),
            )
            migrated["todos"] += 1

    await db.commit()

    total = sum(migrated.values())
    if total > 0:
        logger.info("Migrated %d legacy plain-text rows to encrypted: %s", total, migrated)
    else:
        logger.info("No legacy plain-text rows found — all data already encrypted")

    return migrated
