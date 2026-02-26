import asyncio
import logging
from datetime import datetime, timedelta, timezone

from telegram import Update
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
from bot.utils import make_fallback_command, reply, reply_or_edit
from canvas import client as canvas
from db import models

logger = logging.getLogger(__name__)

SGT = timezone(timedelta(hours=8))

WAITING_NOTE = 0
CAPTURING_QUICKNOTE = 1
WAITING_SEARCH_QUERY = 2


# ── /notes command: list all notes ──


async def notes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    token = await _require_token(update, context)
    if not token:
        return

    user_id = update.effective_user.id
    assignment_notes = await models.get_all_notes(user_id)
    general_notes = await models.get_all_general_notes(user_id)

    if not assignment_notes and not general_notes:
        await reply(
            update.message, context,
            "You don't have any notes yet.\n"
            "Browse /assignments to add notes, or use /start_notes for freeform capture.",
            reply_markup=keyboards.notes_menu(),
        )
        return

    lines = await _format_notes(token, assignment_notes, general_notes)

    await reply(
        update.message, context,
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=keyboards.notes_menu(),
    )


async def notes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    token = await _require_token(update, context)
    if not token:
        return

    user_id = update.effective_user.id
    assignment_notes = await models.get_all_notes(user_id)
    general_notes = await models.get_all_general_notes(user_id)

    if not assignment_notes and not general_notes:
        await reply_or_edit(
            query, context,
            "You don't have any notes yet.\n"
            "Browse /assignments to add notes, or use /start_notes for freeform capture.",
            reply_markup=keyboards.notes_menu(),
        )
        return

    lines = await _format_notes(token, assignment_notes, general_notes)

    await reply_or_edit(
        query, context,
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=keyboards.notes_menu(),
    )


async def _format_notes(
    token: str, assignment_notes: list[dict], general_notes: list[dict]
) -> list[str]:
    """Build MarkdownV2 lines for notes display, including course names."""
    # Build course name lookup
    course_names: dict[int, str] = {}
    if assignment_notes:
        try:
            courses = await canvas.get_courses(token)
            course_names = {c["id"]: c["name"] for c in courses}
        except Exception:
            pass

    lines = ["*Your Notes*\n"]

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

        lines.append("*Assignment Notes*")
        for n, name in zip(assignment_notes, names):
            course_name = course_names.get(n["canvas_course_id"], "Unknown Course")
            lines.append(f"*{_escape_md(name)}*")
            lines.append(f"  _{_escape_md(course_name)}_")
            lines.append(f"  {_escape_md(n['note_text'])}\n")

    if general_notes:
        lines.append("*General Notes*")
        for n in general_notes:
            created_utc = datetime.fromisoformat(n['created_at']).replace(tzinfo=timezone.utc)
            created_sgt = created_utc.astimezone(SGT).strftime('%d %b %Y %H:%M') + " SGT"
            lines.append(f"_{_escape_md(created_sgt)}_")
            lines.append(f"{_escape_md(n['content'])}\n")

    # Truncate if too long for Telegram
    text = "\n".join(lines)
    return [_truncate_message(text)]


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
        lines = await _format_notes(token, assignment_notes, [])
    else:
        general_notes = await models.get_all_general_notes(user_id)
        if not general_notes:
            await reply_or_edit(
                query, context,
                "No general notes yet.\nUse /start_notes for freeform capture.",
                reply_markup=keyboards.back_to_notes(),
            )
            return
        lines = await _format_notes(token, [], general_notes)

    await reply_or_edit(
        query, context,
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=keyboards.back_to_notes(),
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
        entry_points=[CommandHandler("start_notes", start_notes_cmd)],
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
