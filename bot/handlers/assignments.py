import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from bot import keyboards
from canvas import client as canvas
from db import models

logger = logging.getLogger(__name__)


async def _require_token(update: Update) -> str | None:
    """Get the user's Canvas token, or send an error message."""
    user_id = update.effective_user.id
    token = await models.get_canvas_token(user_id)
    if not token:
        text = "You haven't linked your Canvas account yet.\nRun /setup first."
        if update.callback_query:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
    return token


def _format_due(due_str: str | None) -> str:
    if not due_str:
        return "No due date"
    dt = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    diff = dt - now
    days = diff.days
    if days < 0:
        return f"OVERDUE ({dt.strftime('%d %b %H:%M')})"
    if days == 0:
        hours = int(diff.total_seconds() // 3600)
        return f"Due in {hours}h ({dt.strftime('%H:%M')})"
    return f"Due in {days}d ({dt.strftime('%d %b %H:%M')})"


# ── /assignments command ──


async def assignments_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    token = await _require_token(update)
    if not token:
        return
    msg = update.message
    loading = await msg.reply_text("Loading courses...")
    try:
        courses = await canvas.get_courses(token)
    except Exception as e:
        logger.error("Canvas API error: %s", e)
        await loading.edit_text("Failed to fetch courses. Check your Canvas token with /setup.")
        return

    if not courses:
        await loading.edit_text("No active courses found.")
        return

    await loading.edit_text(
        "Select a course:", reply_markup=keyboards.course_list(courses)
    )


async def assignments_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the cmd_assignments callback from menu."""
    query = update.callback_query
    await query.answer()
    token = await _require_token(update)
    if not token:
        return
    try:
        courses = await canvas.get_courses(token)
    except Exception as e:
        logger.error("Canvas API error: %s", e)
        await query.edit_message_text("Failed to fetch courses.")
        return

    if not courses:
        await query.edit_message_text("No active courses found.")
        return

    await query.edit_message_text(
        "Select a course:", reply_markup=keyboards.course_list(courses)
    )


# ── Course selection callback ──


async def course_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    token = await _require_token(update)
    if not token:
        return

    course_id = int(query.data.split("_")[1])
    await query.edit_message_text("Loading assignments and quizzes...")
    try:
        assignments = await canvas.get_assignments(token, course_id)
    except Exception as e:
        logger.error("Canvas API error: %s", e)
        await query.edit_message_text("Failed to fetch assignments.")
        return

    quizzes = []
    try:
        quizzes = await canvas.get_quizzes(token, course_id)
    except Exception:
        logger.debug("Could not fetch quizzes for course %s", course_id)

    if not assignments and not quizzes:
        await query.edit_message_text(
            "No assignments or quizzes found for this course.",
            reply_markup=keyboards.back_to_menu(),
        )
        return

    await query.edit_message_text(
        "Select an item:",
        reply_markup=keyboards.course_items_list(assignments, quizzes, course_id),
    )


# ── Assignment detail callback ──


async def assignment_detail_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    token = await _require_token(update)
    if not token:
        return

    parts = query.data.split("_")
    course_id = int(parts[1])
    assignment_id = int(parts[2])

    try:
        assignment = await canvas.get_assignment(token, course_id, assignment_id)
    except Exception as e:
        logger.error("Canvas API error: %s", e)
        await query.edit_message_text("Failed to fetch assignment details.")
        return

    if not assignment:
        await query.edit_message_text("Assignment not found.")
        return

    user_id = update.effective_user.id
    note = await models.get_note(user_id, assignment_id)
    has_note = note is not None

    due = _format_due(assignment.get("due_at"))
    points = assignment.get("points_possible", "N/A")
    link = canvas.assignment_url(course_id, assignment_id)

    text = (
        f"*{_escape_md(assignment['name'])}*\n\n"
        f"Due: {_escape_md(due)}\n"
        f"Points: {_escape_md(str(points))}\n"
        f"[Open in Canvas]({_escape_url(link)})\n"
    )
    if note:
        text += f"\nYour note: {_escape_md(note)}\n"

    await query.edit_message_text(
        text,
        parse_mode="MarkdownV2",
        reply_markup=keyboards.assignment_detail(course_id, assignment_id, has_note),
    )


# ── Quiz detail callback ──


async def quiz_detail_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    token = await _require_token(update)
    if not token:
        return

    parts = query.data.split("_")
    course_id = int(parts[1])
    quiz_id = int(parts[2])

    try:
        quiz = await canvas.get_quiz(token, course_id, quiz_id)
    except Exception as e:
        logger.error("Canvas API error: %s", e)
        await query.edit_message_text("Failed to fetch quiz details.")
        return

    if not quiz:
        await query.edit_message_text("Quiz not found.")
        return

    due = _format_due(quiz.get("due_at"))
    points = quiz.get("points_possible", "N/A")
    time_limit = quiz.get("time_limit")
    time_str = f"{time_limit} min" if time_limit else "No time limit"
    link = canvas.quiz_url(course_id, quiz_id)

    text = (
        f"*{_escape_md(quiz.get('title', 'Untitled Quiz'))}*\n\n"
        f"Due: {_escape_md(due)}\n"
        f"Points: {_escape_md(str(points))}\n"
        f"Time limit: {_escape_md(time_str)}\n"
        f"[Open in Canvas]({_escape_url(link)})\n"
    )

    await query.edit_message_text(
        text,
        parse_mode="MarkdownV2",
        reply_markup=keyboards.quiz_detail(course_id),
    )


# ── /due command ──


async def _fetch_and_format_due(token: str, days: int) -> str | None:
    """Fetch upcoming assignments and return MarkdownV2 text, or None if empty."""
    upcoming = await canvas.get_upcoming_assignments(token, days=days)
    if not upcoming:
        return None

    lines = [f"*Upcoming Deadlines \\({_escape_md(str(days))} days\\)*\n"]
    for a in upcoming:
        due = _format_due(a.get("due_at"))
        course = a.get("_course_name", "Unknown")
        course_id = a.get("_course_id", 0)
        item_type = a.get("_type", "assignment")
        if item_type == "quiz":
            link = canvas.quiz_url(course_id, a["id"])
        else:
            link = canvas.assignment_url(course_id, a["id"])
        lines.append(f"\\- [{_escape_md(a['name'])}]({_escape_url(link)})")
        lines.append(f"  {_escape_md(course)} \\| {_escape_md(due)}\n")
    return _truncate_message("\n".join(lines))


def _parse_days(args: list[str]) -> int:
    """Parse days from command args, default 7, clamped to 1-90."""
    if args:
        try:
            days = int(args[0])
            return max(1, min(days, 90))
        except ValueError:
            pass
    return 7


async def due_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    token = await _require_token(update)
    if not token:
        return

    days = _parse_days(context.args)
    msg = update.message
    loading = await msg.reply_text("Loading upcoming deadlines...")
    try:
        text = await _fetch_and_format_due(token, days)
    except Exception as e:
        logger.error("Canvas API error: %s", e)
        await loading.edit_text("Failed to fetch assignments.")
        return

    if not text:
        await loading.edit_text(
            f"No assignments due in the next {days} days!",
            reply_markup=keyboards.back_to_menu(),
        )
        return

    await loading.edit_text(
        text,
        parse_mode="MarkdownV2",
        reply_markup=keyboards.back_to_menu(),
    )


async def due_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    token = await _require_token(update)
    if not token:
        return

    days = 7
    await query.edit_message_text("Loading upcoming deadlines...")
    try:
        text = await _fetch_and_format_due(token, days)
    except Exception as e:
        logger.error("Canvas API error: %s", e)
        await query.edit_message_text("Failed to fetch assignments.")
        return

    if not text:
        await query.edit_message_text(
            f"No assignments due in the next {days} days!",
            reply_markup=keyboards.back_to_menu(),
        )
        return

    await query.edit_message_text(
        text,
        parse_mode="MarkdownV2",
        reply_markup=keyboards.back_to_menu(),
    )


def _escape_md(text: str) -> str:
    """Escape special characters for MarkdownV2 text."""
    special = r"\_*[]()~`>#+-=|{}.!"
    escaped = ""
    for ch in str(text):
        if ch in special:
            escaped += f"\\{ch}"
        else:
            escaped += ch
    return escaped


def _escape_url(url: str) -> str:
    """Escape a URL for use inside a MarkdownV2 inline link [text](url).
    Only ) and \\ need escaping inside the URL portion."""
    return str(url).replace("\\", "\\\\").replace(")", "\\)")


TELEGRAM_MSG_LIMIT = 4096


def _truncate_message(text: str, suffix: str = "\n\n\\.\\.\\.message truncated") -> str:
    """Truncate a MarkdownV2 message to fit Telegram's 4096 char limit."""
    if len(text) <= TELEGRAM_MSG_LIMIT:
        return text
    return text[: TELEGRAM_MSG_LIMIT - len(suffix)] + suffix
