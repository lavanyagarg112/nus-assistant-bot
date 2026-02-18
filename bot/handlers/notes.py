import logging

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
from canvas import client as canvas
from db import models

logger = logging.getLogger(__name__)

WAITING_NOTE = 0
CAPTURING_QUICKNOTE = 1


# ── /notes command: list all notes ──


async def notes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    token = await _require_token(update)
    if not token:
        return

    user_id = update.effective_user.id
    assignment_notes = await models.get_all_notes(user_id)
    general_notes = await models.get_all_general_notes(user_id)

    if not assignment_notes and not general_notes:
        await update.message.reply_text(
            "You don't have any notes yet.\n"
            "Browse /assignments to add notes, or use /start_notes for freeform capture.",
            reply_markup=keyboards.back_to_menu(),
        )
        return

    lines = await _format_notes(token, assignment_notes, general_notes)

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=keyboards.back_to_menu(),
    )


async def notes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    token = await _require_token(update)
    if not token:
        return

    user_id = update.effective_user.id
    assignment_notes = await models.get_all_notes(user_id)
    general_notes = await models.get_all_general_notes(user_id)

    if not assignment_notes and not general_notes:
        await query.edit_message_text(
            "You don't have any notes yet.\n"
            "Browse /assignments to add notes, or use /start_notes for freeform capture.",
            reply_markup=keyboards.back_to_menu(),
        )
        return

    lines = await _format_notes(token, assignment_notes, general_notes)

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=keyboards.back_to_menu(),
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
        lines.append("*Assignment Notes*")
        for n in assignment_notes:
            course_name = course_names.get(n["canvas_course_id"], "Unknown Course")
            try:
                assignment = await canvas.get_assignment(
                    token, n["canvas_course_id"], n["canvas_assignment_id"]
                )
                name = assignment["name"] if assignment else f"Assignment #{n['canvas_assignment_id']}"
            except Exception:
                name = f"Assignment #{n['canvas_assignment_id']}"
            lines.append(f"*{_escape_md(name)}*")
            lines.append(f"  _{_escape_md(course_name)}_")
            lines.append(f"  {_escape_md(n['note_text'])}\n")

    if general_notes:
        lines.append("*General Notes*")
        for n in general_notes:
            lines.append(f"_{_escape_md(n['created_at'])}_")
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
        await query.edit_message_text(
            f"Current note: {existing}\n\nType your new note (or /cancel to keep the current one):"
        )
    else:
        await query.edit_message_text("Type your note for this assignment (or /cancel):")

    return WAITING_NOTE


MAX_NOTE_LENGTH = 1000


async def note_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Note can't be empty. Try again or /cancel.")
        return WAITING_NOTE

    if len(text) > MAX_NOTE_LENGTH:
        await update.message.reply_text(
            f"Note is too long ({len(text)} chars). Max is {MAX_NOTE_LENGTH}. Please shorten it."
        )
        return WAITING_NOTE

    course_id = context.user_data.get("note_course_id")
    assignment_id = context.user_data.get("note_assignment_id")

    if not course_id or not assignment_id:
        await update.message.reply_text("Something went wrong. Please try again from /assignments.")
        return ConversationHandler.END

    await models.upsert_note(
        update.effective_user.id, assignment_id, course_id, text
    )
    await update.message.reply_text(
        "Note saved!",
        reply_markup=keyboards.back_to_menu(),
    )
    return ConversationHandler.END


async def note_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled.", reply_markup=keyboards.back_to_menu())
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
        await query.edit_message_text("Note deleted.", reply_markup=keyboards.back_to_menu())
    else:
        await query.edit_message_text("No note found.", reply_markup=keyboards.back_to_menu())


async def unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "I didn't understand that. Type /help to see available commands."
    )


# ── /start_notes / /end_notes quick capture ──


async def start_notes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    registered = await models.is_registered(update.effective_user.id)
    if not registered:
        await update.message.reply_text("You need to /setup first before using notes.")
        return ConversationHandler.END
    context.user_data["quicknote_lines"] = []
    await update.message.reply_text(
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
        await update.message.reply_text(
            f"Note limit reached ({MAX_QUICKNOTE_TOTAL} chars). Send /end_notes to save what you have."
        )
        return CAPTURING_QUICKNOTE

    lines.append(new_text)
    await update.message.reply_text("Got it. Keep going, or send /end_notes to save.")
    return CAPTURING_QUICKNOTE


async def end_notes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lines = context.user_data.pop("quicknote_lines", [])
    if not lines:
        await update.message.reply_text("No notes captured. Notes mode ended.")
        return ConversationHandler.END

    content = "\n".join(lines)
    await models.add_general_note(update.effective_user.id, content)
    await update.message.reply_text(
        "Notes saved! View them anytime with /notes.",
        reply_markup=keyboards.back_to_menu(),
    )
    return ConversationHandler.END


async def quicknote_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("quicknote_lines", None)
    await update.message.reply_text("Notes mode cancelled. Nothing was saved.")
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
        ],
        per_message=False,
    )
