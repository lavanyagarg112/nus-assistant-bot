import asyncio
import logging
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot import keyboards
from bot.handlers.assignments import _escape_md, _require_token, _truncate_message
from bot.utils import check_migration_reminder, make_fallback_command, reply, reply_or_edit
from canvas import client as canvas
from db import models

logger = logging.getLogger(__name__)

SGT = timezone(timedelta(hours=8))

WAITING_NOTE = 0
CAPTURING_QUICKNOTE = 1
WAITING_SEARCH_QUERY = 2
WAITING_GNOTE_DELETE_NUM = 3

ITEMS_PER_PAGE = 10


# ── /notes command: list all notes ──


async def _build_combined_notes(token: str, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> list[dict]:
    """Build a flat list of all notes (assignment + general) with pre-resolved names.

    Each item is a dict with keys: type, display_lines (list of MarkdownV2 strings).
    The result is cached in context.user_data["all_notes_flat"].
    """
    assignment_notes = await models.get_all_notes(user_id)
    general_notes = await models.get_all_general_notes(user_id)

    # Build course name lookup
    course_names: dict[int, str] = {}
    if assignment_notes:
        try:
            courses = await canvas.get_courses(token)
            course_names = {c["id"]: c["name"] for c in courses}
        except Exception:
            pass

    flat: list[dict] = []

    if assignment_notes:
        # Fetch all assignment names in parallel
        async def _get_name(n: dict) -> str:
            try:
                a = await canvas.get_assignment(
                    token, n["canvas_course_id"], n["canvas_assignment_id"]
                )
                return a["name"] if a else f"Assignment #{n['canvas_assignment_id']}"
            except Exception:
                return f"Assignment #{n['canvas_assignment_id']}"

        names = await asyncio.gather(*[_get_name(n) for n in assignment_notes])

        for n, name in zip(assignment_notes, names):
            course_name = course_names.get(n["canvas_course_id"], "Unknown Course")
            flat.append({
                "type": "assignment",
                "summary": f"\\[A\\] *{_escape_md(name)}*\n    _{_escape_md(course_name)}_\n    {_escape_md(n['note_text'][:100])}",
            })

    for n in general_notes:
        created_utc = datetime.fromisoformat(n["created_at"]).replace(tzinfo=timezone.utc)
        created_sgt = created_utc.astimezone(SGT).strftime("%d %b %Y %H:%M") + " SGT"
        flat.append({
            "type": "general",
            "summary": f"\\[G\\] {_escape_md(n['content'][:100])}\n    _{_escape_md(created_sgt)}_",
        })

    context.user_data["all_notes_flat"] = flat
    return flat


def _format_notes_page(flat: list[dict], page: int) -> tuple[str, int]:
    """Format a page of the combined notes list. Returns (text, total_pages)."""
    total = len(flat)
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    start = page * ITEMS_PER_PAGE
    end = min(start + ITEMS_PER_PAGE, total)
    page_items = flat[start:end]

    lines = ["*Your Notes*\n"]
    for i, item in enumerate(page_items, start=start + 1):
        lines.append(f"*{i}\\.* {item['summary']}\n")

    return _truncate_message("\n".join(lines)), total_pages


async def notes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    token = await _require_token(update, context)
    if not token:
        return

    flat = await _build_combined_notes(token, update.effective_user.id, context)

    if not flat:
        await reply(
            update.message, context,
            "You don't have any notes yet.\n"
            "Browse /assignments to add notes, or use /start_notes for freeform capture.",
            reply_markup=keyboards.notes_menu(),
        )
        return

    text, total_pages = _format_notes_page(flat, 0)
    await reply(
        update.message, context,
        text,
        parse_mode="MarkdownV2",
        reply_markup=keyboards.notes_menu(0, total_pages),
    )


async def notes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    token = await _require_token(update, context)
    if not token:
        return

    flat = await _build_combined_notes(token, update.effective_user.id, context)

    if not flat:
        await reply_or_edit(
            query, context,
            "You don't have any notes yet.\n"
            "Browse /assignments to add notes, or use /start_notes for freeform capture.",
            reply_markup=keyboards.notes_menu(),
        )
        return

    text, total_pages = _format_notes_page(flat, 0)
    await reply_or_edit(
        query, context,
        text,
        parse_mode="MarkdownV2",
        reply_markup=keyboards.notes_menu(0, total_pages),
    )


async def notes_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle notes_page_N pagination for combined notes view."""
    query = update.callback_query
    await query.answer()
    token = await _require_token(update, context)
    if not token:
        return

    page = int(query.data.split("_")[2])
    flat = context.user_data.get("all_notes_flat")
    if not flat:
        flat = await _build_combined_notes(token, update.effective_user.id, context)

    if not flat:
        await query.edit_message_text("No notes found.", reply_markup=keyboards.notes_menu())
        return

    text, total_pages = _format_notes_page(flat, page)
    await query.edit_message_text(
        text,
        parse_mode="MarkdownV2",
        reply_markup=keyboards.notes_menu(page, total_pages),
    )


# ── Add/edit note conversation ──


async def note_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    course_id = int(parts[2])
    assignment_id = int(parts[3])

    # Store IDs in user_data for the conversation
    context.user_data["note_course_id"] = course_id
    context.user_data["note_assignment_id"] = assignment_id

    existing = await models.get_note(update.effective_user.id, assignment_id)
    if existing:
        await reply_or_edit(
            query, context,
            f"Current note: {existing}\n\nType your new note (or /cancel to keep the current one):"
        )
    else:
        await reply_or_edit(query, context, "Type your note for this assignment (or /cancel):")

    return WAITING_NOTE


MAX_NOTE_LENGTH = 1000


async def note_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text:
        await reply(update.message, context, "Note can't be empty. Try again or /cancel.")
        return WAITING_NOTE

    if len(text) > MAX_NOTE_LENGTH:
        await reply(
            update.message, context,
            f"Note is too long ({len(text)} chars). Max is {MAX_NOTE_LENGTH}. Please shorten it."
        )
        return WAITING_NOTE

    course_id = context.user_data.pop("note_course_id", None)
    assignment_id = context.user_data.pop("note_assignment_id", None)

    if not course_id or not assignment_id:
        await reply(update.message, context, "Something went wrong. Please try again from /assignments.")
        return ConversationHandler.END

    await models.upsert_note(
        update.effective_user.id, assignment_id, course_id, text
    )
    await reply(
        update.message, context,
        "Note saved!",
        reply_markup=keyboards.back_to_menu(),
    )
    return ConversationHandler.END


async def note_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await reply(update.message, context, "Cancelled.", reply_markup=keyboards.back_to_menu())
    return ConversationHandler.END


# ── Delete note callback ──


async def note_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    course_id = int(parts[2])
    assignment_id = int(parts[3])

    deleted = await models.delete_note(update.effective_user.id, assignment_id)
    if deleted:
        await reply_or_edit(query, context, "Note deleted.", reply_markup=keyboards.back_to_menu())
    else:
        await reply_or_edit(query, context, "No note found.", reply_markup=keyboards.back_to_menu())


# ── Filter notes by type ──


async def notes_filter_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    token = await _require_token(update, context)
    if not token:
        return

    user_id = update.effective_user.id
    # callback_data: "notes_filter_assignment" or "notes_filter_general"
    filter_type = query.data.removeprefix("notes_filter_")

    if filter_type == "assignment":
        assignment_notes = await models.get_all_notes(user_id)
        if not assignment_notes:
            await reply_or_edit(
                query, context,
                "No assignment notes yet.\nBrowse /assignments to add notes.",
                reply_markup=keyboards.back_to_notes(),
            )
            return
        # Build paginated assignment notes
        anotes_flat = await _build_assignment_notes_flat(token, assignment_notes)
        context.user_data["anotes_flat"] = anotes_flat
        page = 0
        text, total_pages = _format_anotes_page(anotes_flat, page)
        await reply_or_edit(
            query, context,
            text,
            parse_mode="MarkdownV2",
            reply_markup=keyboards.assignment_notes_with_pagination(page, total_pages),
        )
    else:
        general_notes = await models.get_all_general_notes(user_id)
        if not general_notes:
            await reply_or_edit(
                query, context,
                "No general notes yet.\nUse /start_notes or /sn for freeform capture.",
                reply_markup=keyboards.back_to_notes(),
            )
            return
        context.user_data["gnotes_list"] = general_notes
        page = 0
        text, markup = _format_general_notes_page(general_notes, page)
        await reply_or_edit(query, context, text, parse_mode="MarkdownV2", reply_markup=markup)


async def _build_assignment_notes_flat(token: str, assignment_notes: list[dict]) -> list[dict]:
    """Build a flat list of assignment notes with resolved names."""
    course_names: dict[int, str] = {}
    try:
        courses = await canvas.get_courses(token)
        course_names = {c["id"]: c["name"] for c in courses}
    except Exception:
        pass

    async def _get_name(n: dict) -> str:
        try:
            a = await canvas.get_assignment(
                token, n["canvas_course_id"], n["canvas_assignment_id"]
            )
            return a["name"] if a else f"Assignment #{n['canvas_assignment_id']}"
        except Exception:
            return f"Assignment #{n['canvas_assignment_id']}"

    names = await asyncio.gather(*[_get_name(n) for n in assignment_notes])
    flat = []
    for n, name in zip(assignment_notes, names):
        course_name = course_names.get(n["canvas_course_id"], "Unknown Course")
        flat.append({
            "summary": f"*{_escape_md(name)}*\n    _{_escape_md(course_name)}_\n    {_escape_md(n['note_text'][:100])}",
        })
    return flat


def _format_anotes_page(flat: list[dict], page: int) -> tuple[str, int]:
    """Format a page of assignment notes. Returns (text, total_pages)."""
    total = len(flat)
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    start = page * ITEMS_PER_PAGE
    end = min(start + ITEMS_PER_PAGE, total)

    lines = ["*Assignment Notes*\n"]
    for i, item in enumerate(flat[start:end], start=start + 1):
        lines.append(f"*{i}\\.* {item['summary']}\n")

    return _truncate_message("\n".join(lines)), total_pages


async def anotes_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle anotes_page_N pagination for assignment notes."""
    query = update.callback_query
    await query.answer()
    token = await _require_token(update, context)
    if not token:
        return

    page = int(query.data.split("_")[2])
    flat = context.user_data.get("anotes_flat")
    if not flat:
        user_id = update.effective_user.id
        assignment_notes = await models.get_all_notes(user_id)
        flat = await _build_assignment_notes_flat(token, assignment_notes)
        context.user_data["anotes_flat"] = flat

    if not flat:
        await query.edit_message_text("No assignment notes.", reply_markup=keyboards.back_to_notes())
        return

    text, total_pages = _format_anotes_page(flat, page)
    await query.edit_message_text(
        text,
        parse_mode="MarkdownV2",
        reply_markup=keyboards.assignment_notes_with_pagination(page, total_pages),
    )


def _format_general_notes_page(
    general_notes: list[dict], page: int
) -> tuple[str, InlineKeyboardMarkup]:
    """Format general notes as a numbered list with pagination."""
    total = len(general_notes)
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    start = page * ITEMS_PER_PAGE
    end = min(start + ITEMS_PER_PAGE, total)
    page_notes = general_notes[start:end]

    lines = ["*General Notes*\n"]
    for i, n in enumerate(page_notes, start=start + 1):
        created_utc = datetime.fromisoformat(n["created_at"]).replace(tzinfo=timezone.utc)
        created_sgt = created_utc.astimezone(SGT).strftime("%d %b %Y %H:%M") + " SGT"
        lines.append(f"*{i}\\.* {_escape_md(n['content'][:100])}")
        lines.append(f"    _{_escape_md(created_sgt)}_\n")

    text = _truncate_message("\n".join(lines))
    markup = keyboards.general_notes_with_delete(page, total_pages)
    return text, markup


async def gnotes_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    page = int(query.data.split("_")[2])
    general_notes = await models.get_all_general_notes(user_id)
    if not general_notes:
        await query.edit_message_text("No general notes.", reply_markup=keyboards.back_to_notes())
        return

    context.user_data["gnotes_list"] = general_notes
    text, markup = _format_general_notes_page(general_notes, page)
    await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=markup)


# ── Delete general note (numbered) ──


async def gnotes_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    gnotes = context.user_data.get("gnotes_list", [])
    if not gnotes:
        await reply_or_edit(query, context, "No notes to delete.", reply_markup=keyboards.back_to_notes())
        return ConversationHandler.END

    context.user_data["delete_gnote_ids"] = [n["id"] for n in gnotes]
    # Send as new message so the numbered list stays visible
    await query.message.reply_text("Send the number of the note to delete (or /cancel):")
    return WAITING_GNOTE_DELETE_NUM


async def gnotes_delete_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    ids = context.user_data.get("delete_gnote_ids", [])

    try:
        num = int(text)
    except ValueError:
        await reply(update.message, context, "Please send a valid number (or /cancel).")
        return WAITING_GNOTE_DELETE_NUM

    if num < 1 or num > len(ids):
        await reply(update.message, context, f"Please send a number between 1 and {len(ids)} (or /cancel).")
        return WAITING_GNOTE_DELETE_NUM

    note_id = ids[num - 1]
    deleted = await models.delete_general_note(note_id, update.effective_user.id)
    context.user_data.pop("delete_gnote_ids", None)

    if deleted:
        await reply(update.message, context, "Note deleted.", reply_markup=keyboards.back_to_notes())
    else:
        await reply(update.message, context, "Note not found.", reply_markup=keyboards.back_to_notes())
    return ConversationHandler.END


async def gnotes_delete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("delete_gnote_ids", None)
    await reply(update.message, context, "Cancelled.", reply_markup=keyboards.back_to_notes())
    return ConversationHandler.END


def get_gnote_delete_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(gnotes_delete_start, pattern=r"^gnotes_delete$"),
        ],
        states={
            WAITING_GNOTE_DELETE_NUM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, gnotes_delete_receive),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", gnotes_delete_cancel),
            MessageHandler(filters.COMMAND, make_fallback_command("notes")),
        ],
        per_message=False,
    )


# ── Search notes ──


async def notes_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await reply_or_edit(query, context, "Type your search query (or /cancel):")
    return WAITING_SEARCH_QUERY


async def notes_search_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query_text = update.message.text.strip()
    if not query_text:
        await reply(update.message, context, "Search query can't be empty. Try again or /cancel.")
        return WAITING_SEARCH_QUERY

    user_id = update.effective_user.id
    token = await models.get_canvas_token(user_id)
    if not token:
        await reply(update.message, context, "You need to /setup first.", reply_markup=keyboards.back_to_menu())
        return ConversationHandler.END

    if await check_migration_reminder(update, context):
        return ConversationHandler.END

    assignment_notes, general_notes = await models.search_notes(user_id, query_text)

    if not assignment_notes and not general_notes:
        await reply(
            update.message, context,
            f"No notes matching \"{query_text}\".",
            reply_markup=keyboards.back_to_notes(),
        )
        return ConversationHandler.END

    lines = await _format_notes(token, assignment_notes, general_notes)
    header = f"*Search results for \"{_escape_md(query_text)}\"*\n\n"
    text = header + "\n".join(lines)

    await reply(
        update.message, context,
        _truncate_message(text),
        parse_mode="MarkdownV2",
        reply_markup=keyboards.back_to_notes(),
    )
    return ConversationHandler.END


async def notes_search_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await reply(update.message, context, "Search cancelled.", reply_markup=keyboards.back_to_notes())
    return ConversationHandler.END


async def unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(
        update.message, context,
        "I didn't understand that. Type /help to see available commands."
    )


# ── /start_notes / /end_notes quick capture ──


async def start_notes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    registered = await models.is_registered(update.effective_user.id)
    if not registered:
        await reply(update.message, context, "You need to /setup first before using notes.")
        return ConversationHandler.END
    if await check_migration_reminder(update, context):
        return ConversationHandler.END
    context.user_data["quicknote_lines"] = []
    await reply(
        update.message, context,
        "Notes mode started. Send me anything and I'll capture it.\n\n"
        "When you're done, send /end_notes to save."
    )
    return CAPTURING_QUICKNOTE


MAX_QUICKNOTE_TOTAL = 5000


async def quicknote_capture(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lines = context.user_data.setdefault("quicknote_lines", [])
    total = sum(len(l) for l in lines)
    new_text = update.message.text.strip()

    if total + len(new_text) > MAX_QUICKNOTE_TOTAL:
        await reply(
            update.message, context,
            f"Note limit reached ({MAX_QUICKNOTE_TOTAL} chars). Send /end_notes to save what you have."
        )
        return CAPTURING_QUICKNOTE

    lines.append(new_text)
    await reply(update.message, context, "Got it. Keep going, or send /end_notes to save.")
    return CAPTURING_QUICKNOTE


async def end_notes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lines = context.user_data.pop("quicknote_lines", [])
    if not lines:
        await reply(update.message, context, "No notes captured. Notes mode ended.")
        return ConversationHandler.END

    content = "\n".join(lines)
    await models.add_general_note(update.effective_user.id, content)
    await reply(
        update.message, context,
        "Notes saved! View them anytime with /notes.",
        reply_markup=keyboards.back_to_menu(),
    )
    return ConversationHandler.END


async def quicknote_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("quicknote_lines", None)
    await reply(update.message, context, "Notes mode cancelled. Nothing was saved.")
    return ConversationHandler.END


def get_quicknote_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("start_notes", start_notes_cmd),
            CommandHandler("sn", start_notes_cmd),
        ],
        states={
            CAPTURING_QUICKNOTE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, quicknote_capture),
            ],
        },
        fallbacks=[
            CommandHandler("end_notes", end_notes_cmd),
            CommandHandler("cancel", quicknote_cancel),
            MessageHandler(filters.COMMAND, make_fallback_command("start_notes")),
        ],
        per_message=False,
    )


def get_search_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(notes_search_start, pattern=r"^notes_search$"),
        ],
        states={
            WAITING_SEARCH_QUERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, notes_search_receive),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", notes_search_cancel),
            MessageHandler(filters.COMMAND, make_fallback_command("search")),
        ],
        per_message=False,
    )


def get_note_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(note_add_start, pattern=r"^note_add_\d+_\d+$"),
        ],
        states={
            WAITING_NOTE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, note_receive),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", note_cancel),
            MessageHandler(filters.COMMAND, make_fallback_command("add note")),
        ],
        per_message=False,
    )
