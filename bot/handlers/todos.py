import logging

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
from bot.utils import fallback_command, reply, reply_or_edit
from canvas import client as canvas
from db import models

logger = logging.getLogger(__name__)

WAITING_TODO_TEXT = 0


# ── /todos — list all active todos ──


async def todos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    registered = await models.is_registered(telegram_id)
    if not registered:
        await reply(update.message, context, "You need to /setup first.")
        return

    show_done = context.args and context.args[0] == "all"
    todos = await models.get_todos(telegram_id, include_done=show_done)

    if not todos:
        msg = "No todos yet! Use /add_todo to create one."
        if not show_done:
            msg += "\nUse /todos all to include completed items."
        await reply(update.message, context, msg, reply_markup=keyboards.back_to_menu())
        return

    text, markup = await _format_todos(telegram_id, todos, show_done)
    await reply(update.message, context, text, parse_mode="MarkdownV2", reply_markup=markup)


async def todos_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    telegram_id = update.effective_user.id

    todos = await models.get_todos(telegram_id, include_done=False)
    if not todos:
        await reply_or_edit(
            query, context,
            "No todos yet! Use /add_todo to create one.",
            reply_markup=keyboards.back_to_menu(),
        )
        return

    text, markup = await _format_todos(telegram_id, todos, False)
    await reply_or_edit(query, context, text, parse_mode="MarkdownV2", reply_markup=markup)


async def _format_todos(
    telegram_id: int, todos: list[dict], show_done: bool
) -> tuple[str, InlineKeyboardMarkup]:
    """Format todos as MarkdownV2 text and build inline keyboard."""
    # Build course name lookup
    course_names: dict[int, str] = {}
    course_ids = {t["canvas_course_id"] for t in todos if t["canvas_course_id"]}
    if course_ids:
        try:
            token = await models.get_canvas_token(telegram_id)
            if token:
                courses = await canvas.get_courses(token)
                course_names = {c["id"]: c["name"] for c in courses}
        except Exception:
            pass

    # Group by course
    grouped: dict[int | None, list[dict]] = {}
    for t in todos:
        grouped.setdefault(t["canvas_course_id"], []).append(t)

    lines = ["*Your TODOs*\n"]
    buttons = []

    for course_id, items in grouped.items():
        if course_id:
            course_name = course_names.get(course_id, f"Course #{course_id}")
            lines.append(f"*{_escape_md(course_name)}*")
        else:
            lines.append("*General*")

        for t in items:
            check = "✅" if t["done"] else "⬜"
            strike = f"~{_escape_md(t['text'])}~" if t["done"] else _escape_md(t["text"])
            lines.append(f"  {check} {strike}")

            # Add toggle and delete buttons for each todo
            toggle_label = "Undo" if t["done"] else "✓ Done"
            buttons.append([
                InlineKeyboardButton(f"{toggle_label}: {t['text'][:20]}", callback_data=f"todotoggle_{t['id']}"),
                InlineKeyboardButton("🗑", callback_data=f"tododel_{t['id']}"),
            ])
        lines.append("")

    if show_done:
        buttons.append([InlineKeyboardButton("Hide Completed", callback_data="todos_active")])
    else:
        buttons.append([InlineKeyboardButton("Show Completed", callback_data="todos_all")])
    buttons.append([InlineKeyboardButton("<< Back to Menu", callback_data="cmd_menu")])
    return _truncate_message("\n".join(lines)), InlineKeyboardMarkup(buttons)


# ── Show all / active todos toggle ──


async def todos_show_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    telegram_id = update.effective_user.id
    show_done = query.data == "todos_all"

    todos = await models.get_todos(telegram_id, include_done=show_done)
    if not todos:
        msg = "No todos yet! Use /add_todo to create one."
        await query.edit_message_text(msg, reply_markup=keyboards.back_to_menu())
        return

    text, markup = await _format_todos(telegram_id, todos, show_done)
    await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=markup)


# ── Toggle todo done/undone ──


def _is_showing_done(query) -> bool:
    """Check if the current view is showing completed todos by inspecting the keyboard."""
    if query.message and query.message.reply_markup:
        for row in query.message.reply_markup.inline_keyboard:
            for btn in row:
                if btn.callback_data == "todos_active":
                    return True  # "Hide Completed" button present → showing done
    return False


async def todo_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    telegram_id = update.effective_user.id
    todo_id = int(query.data.split("_")[1])

    toggled = await models.toggle_todo(todo_id, telegram_id)
    if not toggled:
        await query.edit_message_text("Todo not found.", reply_markup=keyboards.back_to_menu())
        return

    # Re-render the list preserving the current view mode
    show_done = _is_showing_done(query)
    todos = await models.get_todos(telegram_id, include_done=show_done)
    if not todos:
        await query.edit_message_text("All done! No todos left.", reply_markup=keyboards.back_to_menu())
        return

    text, markup = await _format_todos(telegram_id, todos, show_done)
    await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=markup)


# ── Delete todo ──


async def todo_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    telegram_id = update.effective_user.id
    todo_id = int(query.data.split("_")[1])

    await models.delete_todo(todo_id, telegram_id)

    # Re-render the list preserving the current view mode
    show_done = _is_showing_done(query)
    todos = await models.get_todos(telegram_id, include_done=show_done)
    if not todos:
        await query.edit_message_text("No todos left! Use /add_todo to create one.", reply_markup=keyboards.back_to_menu())
        return

    text, markup = await _format_todos(telegram_id, todos, show_done)
    await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=markup)


# ── /add_todo — pick course then type text ──


async def add_todo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    token = await models.get_canvas_token(telegram_id)
    if not token:
        await reply(update.message, context, "You need to /setup first.")
        return

    try:
        courses = await canvas.get_courses(token)
    except Exception:
        await reply(update.message, context, "Failed to fetch courses.")
        return

    buttons = [
        [InlineKeyboardButton(c["name"][:40], callback_data=f"todocourse_{c['id']}")]
        for c in courses
    ]
    buttons.append([InlineKeyboardButton("General (no course)", callback_data="todocourse_0")])
    buttons.append([InlineKeyboardButton("<< Cancel", callback_data="cmd_menu")])

    await reply(
        update.message, context,
        "Which course is this todo for?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def add_todo_course_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    course_id = int(query.data.split("_")[1])
    context.user_data["todo_course_id"] = course_id if course_id != 0 else None

    # Find the course name from the button that was clicked
    course_label = "General"
    if query.message and query.message.reply_markup:
        for row in query.message.reply_markup.inline_keyboard:
            for btn in row:
                if btn.callback_data == query.data:
                    course_label = btn.text
                    break

    await reply_or_edit(query, context, f"Course: {course_label}\n\nType your todo text (or /cancel):")
    return WAITING_TODO_TEXT


MAX_TODO_LENGTH = 500


async def add_todo_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text:
        await reply(update.message, context, "Todo can't be empty. Try again or /cancel.")
        return WAITING_TODO_TEXT

    if len(text) > MAX_TODO_LENGTH:
        await reply(
            update.message, context,
            f"Todo is too long ({len(text)} chars). Max is {MAX_TODO_LENGTH}. Please shorten it."
        )
        return WAITING_TODO_TEXT

    telegram_id = update.effective_user.id
    course_id = context.user_data.pop("todo_course_id", None)

    await models.add_todo(telegram_id, text, course_id)
    await reply(update.message, context, "Todo added! View with /todos.", reply_markup=keyboards.back_to_menu())
    return ConversationHandler.END


async def add_todo_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("todo_course_id", None)
    await reply(update.message, context, "Cancelled.", reply_markup=keyboards.back_to_menu())
    return ConversationHandler.END


def get_add_todo_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(add_todo_course_callback, pattern=r"^todocourse_\d+$"),
        ],
        states={
            WAITING_TODO_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_todo_receive),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", add_todo_cancel),
            MessageHandler(filters.COMMAND, fallback_command),
        ],
        per_message=False,
    )
