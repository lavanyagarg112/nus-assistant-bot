import asyncio
import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot import keyboards
from bot.utils import breadcrumb, reply, reply_or_edit
from canvas import client as canvas
from db import models

logger = logging.getLogger(__name__)


async def _require_token(update: Update, context: ContextTypes.DEFAULT_TYPE = None) -> str | None:
    """Get the user's Canvas token, or send an error message."""
    user_id = update.effective_user.id
    token = await models.get_canvas_token(user_id)
    if not token:
        text = "You haven't linked your Canvas account yet.\nRun /setup first."
        if update.callback_query:
            if context:
                await reply_or_edit(update.callback_query, context, text, reply_markup=keyboards.back_to_menu())
            else:
                await update.callback_query.edit_message_text(text, reply_markup=keyboards.back_to_menu())
        else:
            if context:
                await reply(update.message, context, text, reply_markup=keyboards.back_to_menu())
            else:
                await update.message.reply_text(text, reply_markup=keyboards.back_to_menu())
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
        total_mins = int(diff.total_seconds() // 60)
        if total_mins < 60:
            return f"Due in {total_mins}m ({dt.strftime('%H:%M')})"
        hours = total_mins // 60
        return f"Due in {hours}h ({dt.strftime('%H:%M')})"
    return f"Due in {days}d ({dt.strftime('%d %b %H:%M')})"


# ── /assignments command ──


async def assignments_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    token = await _require_token(update, context)
    if not token:
        return
    msg = update.message
    loading = await reply(msg, context, "Loading courses...")
    try:
        courses = await canvas.get_courses(token)
    except Exception:
        logger.error("Canvas API error fetching courses for user %s", update.effective_user.id)
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
    token = await _require_token(update, context)
    if not token:
        return
    try:
        courses = await canvas.get_courses(token)
    except Exception:
        logger.error("Canvas API error fetching courses for user %s", update.effective_user.id)
        await reply_or_edit(query, context, "Failed to fetch courses.")
        return

    if not courses:
        await reply_or_edit(query, context, "No active courses found.")
        return

    await reply_or_edit(
        query, context, "Select a course:", reply_markup=keyboards.course_list(courses)
    )


# ── Course selection callback ──


async def course_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    token = await _require_token(update, context)
    if not token:
        return

    course_id = int(query.data.split("_")[1])

    await query.edit_message_text("Loading assignments and quizzes...")

    # Fetch course name, assignments, and quizzes in parallel
    course_name_task = _course_name(token, course_id)
    assignments_task = canvas.get_assignments(token, course_id)
    quizzes_task = canvas.get_quizzes(token, course_id)

    results = await asyncio.gather(
        course_name_task, assignments_task, quizzes_task,
        return_exceptions=True,
    )

    course_name = results[0] if not isinstance(results[0], Exception) else None

    if isinstance(results[1], Exception):
        logger.error("Canvas API error fetching assignments for user %s", update.effective_user.id)
        await query.edit_message_text("Failed to fetch assignments.")
        return
    assignments = results[1]

    quizzes = results[2] if not isinstance(results[2], Exception) else []

    path = breadcrumb("Assignments", course_name) if course_name else "Assignments"

    if not assignments and not quizzes:
        await query.edit_message_text(
            f"{path}\n\nNo assignments or quizzes found for this course.",
            reply_markup=keyboards.back_to_menu(),
        )
        return

    await query.edit_message_text(
        f"{path}\n\nSelect an item:",
        reply_markup=keyboards.course_items_list(assignments, quizzes, course_id),
    )


async def _course_name(token: str, course_id: int) -> str | None:
    """Look up a course name from the cached course list."""
    try:
        courses = await canvas.get_courses(token)
        for c in courses:
            if c["id"] == course_id:
                return c["name"]
    except Exception:
        pass
    return None


# ── Assignment detail callback ──


async def assignment_detail_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    token = await _require_token(update, context)
    if not token:
        return

    parts = query.data.split("_")
    course_id = int(parts[1])
    assignment_id = int(parts[2])

    try:
        assignment = await canvas.get_assignment(token, course_id, assignment_id)
    except Exception:
        logger.error("Canvas API error fetching assignment for user %s", update.effective_user.id)
        await query.edit_message_text("Failed to fetch assignment details.")
        return

    if not assignment:
        await query.edit_message_text("Assignment not found.")
        return

    user_id = update.effective_user.id
    note = await models.get_note(user_id, assignment_id)
    has_note = note is not None

    course_name = await _course_name(token, course_id)
    asgn_name = assignment['name']
    due = _format_due(assignment.get("due_at"))
    points = assignment.get("points_possible", "N/A")
    status = canvas.submission_status_text(assignment)
    link = canvas.assignment_url(course_id, assignment_id)

    parts = ["Assignments"]
    if course_name:
        parts.append(course_name)
    parts.append(asgn_name)
    path = _escape_md(breadcrumb(*parts))

    text = (
        f"{path}\n\n"
        f"Due: {_escape_md(due)}\n"
        f"Points: {_escape_md(str(points))}\n"
        f"Status: {_escape_md(status)}\n"
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
    token = await _require_token(update, context)
    if not token:
        return

    parts = query.data.split("_")
    course_id = int(parts[1])
    quiz_id = int(parts[2])

    try:
        quiz = await canvas.get_quiz(token, course_id, quiz_id)
    except Exception:
        logger.error("Canvas API error fetching quiz for user %s", update.effective_user.id)
        await query.edit_message_text("Failed to fetch quiz details.")
        return

    if not quiz:
        await query.edit_message_text("Quiz not found.")
        return

    course_name = await _course_name(token, course_id)
    quiz_name = quiz.get('title', 'Untitled Quiz')
    due = _format_due(quiz.get("due_at"))
    points = quiz.get("points_possible", "N/A")
    time_limit = quiz.get("time_limit")
    time_str = f"{time_limit} min" if time_limit else "No time limit"
    link = canvas.quiz_url(course_id, quiz_id)

    # Fetch quiz submission status
    try:
        quiz_sub = await canvas.get_quiz_submission(token, course_id, quiz_id)
        submitted = quiz_sub and quiz_sub.get("workflow_state") in ("complete", "pending_review")
    except Exception:
        submitted = False
    quiz["_type"] = "quiz"
    quiz["_submitted"] = submitted
    status = canvas.submission_status_text(quiz)

    parts = ["Assignments"]
    if course_name:
        parts.append(course_name)
    parts.append(quiz_name)
    path = _escape_md(breadcrumb(*parts))

    text = (
        f"{path}\n\n"
        f"Due: {_escape_md(due)}\n"
        f"Points: {_escape_md(str(points))}\n"
        f"Time limit: {_escape_md(time_str)}\n"
        f"Status: {_escape_md(status)}\n"
        f"[Open in Canvas]({_escape_url(link)})\n"
    )

    await query.edit_message_text(
        text,
        parse_mode="MarkdownV2",
        reply_markup=keyboards.quiz_detail(course_id),
    )


# ── /due command ──


async def _fetch_and_format_due(
    token: str, days: int, show_submitted: bool = False
) -> tuple[str | None, InlineKeyboardMarkup]:
    """Fetch upcoming assignments and return (MarkdownV2 text, keyboard).

    Returns (None, keyboard) if no items at all.
    """
    upcoming = await canvas.get_upcoming_assignments(token, days=days)
    markup = keyboards.due_list(show_submitted, days)

    if not upcoming:
        return None, markup

    pending = [a for a in upcoming if not canvas.is_submitted(a)]
    submitted = [a for a in upcoming if canvas.is_submitted(a)]

    lines = [f"*Upcoming Deadlines \\({_escape_md(str(days))} days\\)*\n"]

    if pending:
        for a in pending:
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
    else:
        lines.append("_All items have been submitted\\!_\n")

    if show_submitted and submitted:
        lines.append(f"\n*Submitted*\n")
        for a in submitted:
            due = _format_due(a.get("due_at"))
            course = a.get("_course_name", "Unknown")
            course_id = a.get("_course_id", 0)
            item_type = a.get("_type", "assignment")
            if item_type == "quiz":
                link = canvas.quiz_url(course_id, a["id"])
            else:
                link = canvas.assignment_url(course_id, a["id"])
            lines.append(f"\\- \u2705 [{_escape_md(a['name'])}]({_escape_url(link)})")
            lines.append(f"  {_escape_md(course)} \\| {_escape_md(due)}\n")
    elif submitted:
        lines.append(f"_{_escape_md(str(len(submitted)))} submitted item\\(s\\) hidden_")

    return _truncate_message("\n".join(lines)), markup


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
    token = await _require_token(update, context)
    if not token:
        return

    days = _parse_days(context.args)
    msg = update.message
    loading = await reply(msg, context, "Loading upcoming deadlines...")
    try:
        text, markup = await _fetch_and_format_due(token, days)
    except Exception:
        logger.error("Canvas API error fetching deadlines for user %s", update.effective_user.id)
        await loading.edit_text("Failed to fetch assignments.")
        return

    if not text:
        await loading.edit_text(
            f"No assignments due in the next {days} days!",
            reply_markup=markup,
        )
        return

    await loading.edit_text(
        text,
        parse_mode="MarkdownV2",
        reply_markup=markup,
    )


async def due_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    token = await _require_token(update, context)
    if not token:
        return

    days = 7
    loading = await reply_or_edit(query, context, "Loading upcoming deadlines...")
    try:
        text, markup = await _fetch_and_format_due(token, days)
    except Exception:
        logger.error("Canvas API error fetching deadlines for user %s", update.effective_user.id)
        await loading.edit_text("Failed to fetch assignments.")
        return

    if not text:
        await loading.edit_text(
            f"No assignments due in the next {days} days!",
            reply_markup=markup,
        )
        return

    await loading.edit_text(
        text,
        parse_mode="MarkdownV2",
        reply_markup=markup,
    )


async def due_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Show/Hide Submitted toggle on the due view."""
    query = update.callback_query
    await query.answer()
    token = await _require_token(update, context)
    if not token:
        return

    parts = query.data.split("_")
    show_submitted = parts[1] == "show"
    days = int(parts[3]) if len(parts) > 3 else 7

    try:
        text, markup = await _fetch_and_format_due(token, days, show_submitted=show_submitted)
    except Exception:
        logger.error("Canvas API error fetching deadlines for user %s", update.effective_user.id)
        await query.edit_message_text("Failed to fetch assignments.")
        return

    if not text:
        await query.edit_message_text(
            f"No assignments due in the next {days} days!",
            reply_markup=markup,
        )
        return

    await query.edit_message_text(
        text,
        parse_mode="MarkdownV2",
        reply_markup=markup,
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
