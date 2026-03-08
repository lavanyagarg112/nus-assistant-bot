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
from bot.handlers.assignments import _escape_md, _split_message, TOKEN_EXPIRED_MSG
from bot.utils import check_migration_reminder, make_fallback_command, reply, reply_or_edit
from canvas.client import CanvasTokenError
from canvas import client as canvas
from db import models

logger = logging.getLogger(__name__)

WAITING_TODO_TEXT = 0
WAITING_TODO_DELETE_NUM = 1
WAITING_TODO_TOGGLE_NUM = 2

ITEMS_PER_PAGE = 10


# ── /todos — list all active todos ──


async def todos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    registered = await models.is_registered(telegram_id)
    if not registered:
        await reply(update.message, context, "You need to /setup first.")
        return

    if await check_migration_reminder(update, context):
        return

    show_done = context.args and context.args[0] == "all"
    todos = await models.get_todos(telegram_id, include_done=show_done)

    if not todos:
        msg = "No todos yet! Use /add_todo to create one."
        if not show_done:
            msg += "\nUse /todos all to include completed items."
        await reply(update.message, context, msg, reply_markup=keyboards.back_to_menu())
        return

    context.user_data["todos_show_done"] = show_done
    context.user_data["todos_list"] = todos
    page = 0
    chunks, markup = await _format_todos(telegram_id, todos, show_done, page)
    for extra in chunks[:-1]:
        await reply(update.message, context, extra, parse_mode="MarkdownV2")
    await reply(update.message, context, chunks[-1], parse_mode="MarkdownV2", reply_markup=markup)


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

    context.user_data["todos_show_done"] = False
    context.user_data["todos_list"] = todos
    page = 0
    chunks, markup = await _format_todos(telegram_id, todos, False, page)
    for extra in chunks[:-1]:
        await query.message.reply_text(extra, parse_mode="MarkdownV2")
    await reply_or_edit(query, context, chunks[-1], parse_mode="MarkdownV2", reply_markup=markup)


async def _format_todos(
    telegram_id: int, todos: list[dict], show_done: bool, page: int = 0
) -> tuple[str, InlineKeyboardMarkup]:
    """Format todos as a numbered MarkdownV2 list with pagination keyboard."""
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

    total = len(todos)
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    start = page * ITEMS_PER_PAGE
    end = min(start + ITEMS_PER_PAGE, total)
    page_todos = todos[start:end]

    lines = ["*Your TODOs*\n"]

    for i, t in enumerate(page_todos, start=start + 1):
        check = "✅" if t["done"] else "⬜"
        strike = f"~{_escape_md(t['text'])}~" if t["done"] else _escape_md(t["text"])
        course_id = t["canvas_course_id"]
        if course_id:
            course_name = course_names.get(course_id, f"Course #{course_id}")
            lines.append(f"*{i}\\.* {check} {strike}")
            lines.append(f"    _{_escape_md(course_name)}_")
        else:
            lines.append(f"*{i}\\.* {check} {strike}")
        lines.append("")

    markup = keyboards.todos_list_keyboard(show_done, page, total_pages)
    return _split_message("\n".join(lines)), markup


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

    context.user_data["todos_show_done"] = show_done
    context.user_data["todos_list"] = todos
    chunks, markup = await _format_todos(telegram_id, todos, show_done, 0)
    for extra in chunks[:-1]:
        await query.message.reply_text(extra, parse_mode="MarkdownV2")
    await query.edit_message_text(chunks[-1], parse_mode="MarkdownV2", reply_markup=markup)


async def todos_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    telegram_id = update.effective_user.id

    page = int(query.data.split("_")[2])
    show_done = context.user_data.get("todos_show_done", False)
    todos = await models.get_todos(telegram_id, include_done=show_done)
    if not todos:
        await query.edit_message_text("No todos.", reply_markup=keyboards.back_to_menu())
        return

    context.user_data["todos_list"] = todos
    chunks, markup = await _format_todos(telegram_id, todos, show_done, page)
    for extra in chunks[:-1]:
        await query.message.reply_text(extra, parse_mode="MarkdownV2")
    await query.edit_message_text(chunks[-1], parse_mode="MarkdownV2", reply_markup=markup)


# ── Toggle todo (numbered conversation) ──


async def todos_toggle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    todos = context.user_data.get("todos_list", [])
    if not todos:
        await reply_or_edit(query, context, "No todos to toggle.", reply_markup=keyboards.back_to_menu())
        return ConversationHandler.END

    context.user_data["toggle_todo_ids"] = [t["id"] for t in todos]
    # Send as new message so the numbered list stays visible
    await query.message.reply_text("Send the number of the todo to toggle done/undone (or /cancel):")
    return WAITING_TODO_TOGGLE_NUM


async def todos_toggle_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    ids = context.user_data.get("toggle_todo_ids", [])

    try:
        num = int(text)
    except ValueError:
        await reply(update.message, context, "Please send a valid number (or /cancel).")
        return WAITING_TODO_TOGGLE_NUM

    if num < 1 or num > len(ids):
        await reply(update.message, context, f"Please send a number between 1 and {len(ids)} (or /cancel).")
        return WAITING_TODO_TOGGLE_NUM

    todo_id = ids[num - 1]
    toggled = await models.toggle_todo(todo_id, update.effective_user.id)
    context.user_data.pop("toggle_todo_ids", None)

    if toggled:
        await reply(update.message, context, "Todo updated!", reply_markup=keyboards.back_to_menu())
    else:
        await reply(update.message, context, "Todo not found.", reply_markup=keyboards.back_to_menu())
    return ConversationHandler.END


async def todos_toggle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("toggle_todo_ids", None)
    await reply(update.message, context, "Cancelled.", reply_markup=keyboards.back_to_menu())
    return ConversationHandler.END


# ── Delete todo (numbered conversation) ──


async def todos_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    todos = context.user_data.get("todos_list", [])
    if not todos:
        await reply_or_edit(query, context, "No todos to delete.", reply_markup=keyboards.back_to_menu())
        return ConversationHandler.END

    context.user_data["delete_todo_ids"] = [t["id"] for t in todos]
    # Send as new message so the numbered list stays visible
    await query.message.reply_text("Send the number of the todo to delete (or /cancel):")
    return WAITING_TODO_DELETE_NUM


async def todos_delete_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    ids = context.user_data.get("delete_todo_ids", [])

    try:
        num = int(text)
    except ValueError:
        await reply(update.message, context, "Please send a valid number (or /cancel).")
        return WAITING_TODO_DELETE_NUM

    if num < 1 or num > len(ids):
        await reply(update.message, context, f"Please send a number between 1 and {len(ids)} (or /cancel).")
        return WAITING_TODO_DELETE_NUM

    todo_id = ids[num - 1]
    await models.delete_todo(todo_id, update.effective_user.id)
    context.user_data.pop("delete_todo_ids", None)

    await reply(update.message, context, "Todo deleted.", reply_markup=keyboards.back_to_menu())
    return ConversationHandler.END


async def todos_delete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("delete_todo_ids", None)
    await reply(update.message, context, "Cancelled.", reply_markup=keyboards.back_to_menu())
    return ConversationHandler.END


# ── /add_todo — pick course then type text ──


async def add_todo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    token = await models.get_canvas_token(telegram_id)
    if not token:
        await reply(update.message, context, "You need to /setup first.")
        return

    if await check_migration_reminder(update, context):
        return

    try:
        courses = await canvas.get_courses(token)
    except CanvasTokenError:
        await reply(update.message, context, TOKEN_EXPIRED_MSG)
        return
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
            MessageHandler(filters.COMMAND, make_fallback_command("add_todo")),
        ],
        per_message=False,
    )


def get_todo_toggle_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(todos_toggle_start, pattern=r"^todos_toggle_start$"),
        ],
        states={
            WAITING_TODO_TOGGLE_NUM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, todos_toggle_receive),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", todos_toggle_cancel),
            MessageHandler(filters.COMMAND, make_fallback_command("todos")),
        ],
        per_message=False,
    )


def get_todo_delete_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(todos_delete_start, pattern=r"^todos_delete_start$"),
        ],
        states={
            WAITING_TODO_DELETE_NUM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, todos_delete_receive),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", todos_delete_cancel),
            MessageHandler(filters.COMMAND, make_fallback_command("todos")),
        ],
        per_message=False,
    )
