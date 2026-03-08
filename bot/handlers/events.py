import logging
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot import keyboards
from bot.handlers.assignments import _escape_md, _truncate_message
from bot.utils import make_fallback_command, reply, reply_or_edit
from db import models

logger = logging.getLogger(__name__)

SGT = timezone(timedelta(hours=8))

# Conversation states
WAITING_EVENT_TYPE = 0
WAITING_EVENT_TITLE = 1
WAITING_EVENT_DATE = 2
WAITING_EVENT_VENUE = 3
WAITING_EVENT_NOTES = 4
WAITING_EVENT_DELETE_NUM = 5

ITEMS_PER_PAGE = 10


# ── /events command: list upcoming events ──


async def events_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    registered = await models.is_registered(update.effective_user.id)
    if not registered:
        await reply(update.message, context, "You need to /setup first.")
        return

    events = await models.get_events(update.effective_user.id)
    if not events:
        await reply(
            update.message, context,
            "No upcoming events. Use /add_event to add an exam or assignment.",
            reply_markup=keyboards.back_to_menu(),
        )
        return

    page = 0
    text, markup = _format_events(events, page)
    context.user_data["events_list"] = events
    await reply(update.message, context, text, parse_mode="MarkdownV2", reply_markup=markup)


async def events_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    events = await models.get_events(update.effective_user.id)
    if not events:
        await reply_or_edit(
            query, context,
            "No upcoming events. Use /add_event to add an exam or assignment.",
            reply_markup=keyboards.back_to_menu(),
        )
        return

    page = 0
    text, markup = _format_events(events, page)
    context.user_data["events_list"] = events
    await reply_or_edit(query, context, text, parse_mode="MarkdownV2", reply_markup=markup)


def _format_events(events: list[dict], page: int) -> tuple[str, InlineKeyboardMarkup]:
    total = len(events)
    start = page * ITEMS_PER_PAGE
    end = min(start + ITEMS_PER_PAGE, total)
    page_events = events[start:end]

    lines = ["*Your Events*\n"]
    for i, e in enumerate(page_events, start=start + 1):
        tag = "Exam" if e["type"] == "exam" else "Assignment"
        due_dt = datetime.fromisoformat(e["due_at"]).astimezone(SGT)
        due_str = due_dt.strftime("%d %b %Y %H:%M") + " SGT"
        lines.append(f"*{i}\\.*  \\[{_escape_md(tag)}\\] {_escape_md(e['title'])}")
        date_label = "Exam" if e["type"] == "exam" else "Due"
        lines.append(f"    {_escape_md(date_label)}: {_escape_md(due_str)}")
        if e.get("venue"):
            lines.append(f"    Venue: {_escape_md(e['venue'])}")
        if e.get("notes"):
            lines.append(f"    Notes: {_escape_md(e['notes'])}")
        lines.append("")

    buttons = []
    # Pagination
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("< Prev", callback_data=f"events_page_{page - 1}"))
    if end < total:
        nav_row.append(InlineKeyboardButton("Next >", callback_data=f"events_page_{page + 1}"))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton("Delete Event", callback_data="events_delete")])
    buttons.append([InlineKeyboardButton("<< Back to Menu", callback_data="cmd_menu")])

    return _truncate_message("\n".join(lines)), InlineKeyboardMarkup(buttons)


async def events_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    page = int(query.data.split("_")[2])
    events = await models.get_events(update.effective_user.id)
    if not events:
        await query.edit_message_text("No events found.", reply_markup=keyboards.back_to_menu())
        return

    context.user_data["events_list"] = events
    text, markup = _format_events(events, page)
    await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=markup)


# ── Delete event (numbered) ──


async def events_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    events = context.user_data.get("events_list", [])
    if not events:
        await reply_or_edit(query, context, "No events to delete.", reply_markup=keyboards.back_to_menu())
        return ConversationHandler.END

    # Store IDs for mapping
    context.user_data["delete_event_ids"] = [e["id"] for e in events]
    # Send as new message so the numbered list stays visible
    await query.message.reply_text("Send the number of the event to delete (or /cancel):")
    return WAITING_EVENT_DELETE_NUM


async def events_delete_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    ids = context.user_data.get("delete_event_ids", [])

    try:
        num = int(text)
    except ValueError:
        await reply(update.message, context, "Please send a valid number (or /cancel).")
        return WAITING_EVENT_DELETE_NUM

    if num < 1 or num > len(ids):
        await reply(update.message, context, f"Please send a number between 1 and {len(ids)} (or /cancel).")
        return WAITING_EVENT_DELETE_NUM

    event_id = ids[num - 1]
    deleted = await models.delete_event(event_id, update.effective_user.id)
    context.user_data.pop("delete_event_ids", None)

    if deleted:
        await reply(update.message, context, "Event deleted.", reply_markup=keyboards.back_to_menu())
    else:
        await reply(update.message, context, "Event not found.", reply_markup=keyboards.back_to_menu())
    return ConversationHandler.END


async def events_delete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("delete_event_ids", None)
    await reply(update.message, context, "Cancelled.", reply_markup=keyboards.back_to_menu())
    return ConversationHandler.END


# ── /add_event conversation ──


async def add_event_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    registered = await models.is_registered(update.effective_user.id)
    if not registered:
        await reply(update.message, context, "You need to /setup first.")
        return

    await reply(
        update.message, context,
        "What type of event?",
        reply_markup=keyboards.event_type_picker(),
    )


async def add_event_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    event_type = query.data.removeprefix("eventtype_")
    context.user_data["event_type"] = event_type

    await reply_or_edit(query, context, "Enter the title (or /cancel):")
    return WAITING_EVENT_TITLE


async def add_event_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text:
        await reply(update.message, context, "Title can't be empty. Try again or /cancel.")
        return WAITING_EVENT_TITLE

    if len(text) > 200:
        await reply(update.message, context, "Title is too long (max 200 chars). Please shorten it.")
        return WAITING_EVENT_TITLE

    context.user_data["event_title"] = text
    event_type = context.user_data.get("event_type", "assignment")
    date_label = "exam date" if event_type == "exam" else "due date"
    await reply(
        update.message, context,
        f"Enter the {date_label} in DD/MM/YYYY HH:MM format (24h, SGT).\nExample: 15/03/2026 23:59\n\nOr /cancel.",
    )
    return WAITING_EVENT_DATE


async def add_event_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        dt = datetime.strptime(text, "%d/%m/%Y %H:%M")
        dt_sgt = dt.replace(tzinfo=SGT)
        due_iso = dt_sgt.astimezone(timezone.utc).isoformat()
    except ValueError:
        await reply(
            update.message, context,
            "Invalid format. Please use DD/MM/YYYY HH:MM (e.g. 15/03/2026 23:59) or /cancel.",
        )
        return WAITING_EVENT_DATE

    context.user_data["event_due"] = due_iso
    await reply(update.message, context, "Enter the venue (or /skip to skip, /cancel to cancel):")
    return WAITING_EVENT_VENUE


async def add_event_venue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text.lower() == "/skip":
        context.user_data["event_venue"] = None
    else:
        if len(text) > 200:
            await reply(update.message, context, "Venue is too long (max 200 chars). Please shorten it.")
            return WAITING_EVENT_VENUE
        context.user_data["event_venue"] = text

    await reply(update.message, context, "Any additional notes? (or /skip to skip, /cancel to cancel):")
    return WAITING_EVENT_NOTES


async def add_event_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text.lower() == "/skip":
        event_notes = None
    else:
        if len(text) > 500:
            await reply(update.message, context, "Notes too long (max 500 chars). Please shorten.")
            return WAITING_EVENT_NOTES
        event_notes = text

    telegram_id = update.effective_user.id
    event_type = context.user_data.pop("event_type", "assignment")
    title = context.user_data.pop("event_title", "Untitled")
    due_iso = context.user_data.pop("event_due", "")
    venue = context.user_data.pop("event_venue", None)

    await models.add_event(telegram_id, event_type, title, due_iso, venue, event_notes)
    await reply(
        update.message, context,
        "Event added! View with /events.",
        reply_markup=keyboards.back_to_menu(),
    )
    return ConversationHandler.END


async def add_event_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for key in ("event_type", "event_title", "event_due", "event_venue"):
        context.user_data.pop(key, None)
    await reply(update.message, context, "Cancelled.", reply_markup=keyboards.back_to_menu())
    return ConversationHandler.END


async def add_event_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /skip in venue or notes state — delegate to the correct handler."""
    # This won't be called directly; /skip is handled as text in venue/notes states
    return ConversationHandler.END


def get_add_event_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(add_event_type_callback, pattern=r"^eventtype_(exam|assignment)$"),
        ],
        states={
            WAITING_EVENT_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_title),
            ],
            WAITING_EVENT_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_date),
            ],
            WAITING_EVENT_VENUE: [
                CommandHandler("skip", lambda u, c: add_event_venue(u, c)),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_venue),
            ],
            WAITING_EVENT_NOTES: [
                CommandHandler("skip", lambda u, c: add_event_notes(u, c)),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_notes),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", add_event_cancel),
            MessageHandler(filters.COMMAND, make_fallback_command("add_event")),
        ],
        per_message=False,
    )


def get_event_delete_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(events_delete_start, pattern=r"^events_delete$"),
        ],
        states={
            WAITING_EVENT_DELETE_NUM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, events_delete_receive),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", events_delete_cancel),
            MessageHandler(filters.COMMAND, make_fallback_command("events")),
        ],
        per_message=False,
    )
