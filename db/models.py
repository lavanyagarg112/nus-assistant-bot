from cryptography.fernet import Fernet
import config
from db.database import get_db

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
        (telegram_id, canvas_assignment_id, canvas_course_id, note_text),
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
    return rows[0][0]


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
            "note_text": r[2],
            "updated_at": r[3],
        }
        for r in rows
    ]


async def add_general_note(telegram_id: int, content: str) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO general_notes (telegram_id, content) VALUES (?, ?)",
        (telegram_id, content),
    )
    await db.commit()


async def get_all_general_notes(telegram_id: int) -> list[dict]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id, content, created_at FROM general_notes WHERE telegram_id = ? ORDER BY created_at DESC",
        (telegram_id,),
    )
    return [{"id": r[0], "content": r[1], "created_at": r[2]} for r in rows]


# ── Todos CRUD ──


async def add_todo(telegram_id: int, text: str, canvas_course_id: int | None = None) -> int:
    db = await get_db()
    cursor = await db.execute(
        "INSERT INTO todos (telegram_id, canvas_course_id, text) VALUES (?, ?, ?)",
        (telegram_id, canvas_course_id, text),
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
        {"id": r[0], "canvas_course_id": r[1], "text": r[2], "done": r[3], "created_at": r[4]}
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


async def search_notes(telegram_id: int, query: str) -> tuple[list[dict], list[dict]]:
    """Search assignment notes and general notes by keyword. Returns (assignment_notes, general_notes)."""
    db = await get_db()
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like_pattern = f"%{escaped}%"
    a_rows = await db.execute_fetchall(
        """
        SELECT canvas_assignment_id, canvas_course_id, note_text, updated_at
        FROM notes WHERE telegram_id = ? AND note_text LIKE ? ESCAPE '\\'
        ORDER BY updated_at DESC
        """,
        (telegram_id, like_pattern),
    )
    assignment_notes = [
        {"canvas_assignment_id": r[0], "canvas_course_id": r[1], "note_text": r[2], "updated_at": r[3]}
        for r in a_rows
    ]
    g_rows = await db.execute_fetchall(
        """
        SELECT id, content, created_at
        FROM general_notes WHERE telegram_id = ? AND content LIKE ? ESCAPE '\\'
        ORDER BY created_at DESC
        """,
        (telegram_id, like_pattern),
    )
    general_notes = [{"id": r[0], "content": r[1], "created_at": r[2]} for r in g_rows]
    return assignment_notes, general_notes


async def delete_note(telegram_id: int, canvas_assignment_id: int) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM notes WHERE telegram_id = ? AND canvas_assignment_id = ?",
        (telegram_id, canvas_assignment_id),
    )
    await db.commit()
    return cursor.rowcount > 0
