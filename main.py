import logging
from datetime import datetime, time, timezone, timedelta

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

import config
from bot.handlers import assignments, files, notes, settings, start, todos
from canvas import client as canvas
from db import models
from db.database import close_db, init_db

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# Suppress httpx request logging — it leaks the bot token in URLs
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

SGT = timezone(timedelta(hours=8))


async def post_init(application: Application) -> None:
    await init_db()
    logger.info("Database initialized")


async def post_shutdown(application: Application) -> None:
    await close_db()
    logger.info("Database connection closed")


async def error_handler(update: object, context) -> None:
    """Global error handler — log the error and notify the user if possible."""
    logger.error("Unhandled exception:", exc_info=context.error)

    if update and hasattr(update, "effective_chat") and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Something went wrong. Please try again or type /help.",
            )
        except Exception:
            pass


# ── Daily reminder job ──


async def hourly_reminder(context) -> None:
    """Check which users want reminders at the current SGT hour and send them."""
    now_sgt = datetime.now(SGT)
    current_hour = now_sgt.hour

    user_ids = await models.get_users_for_reminder_hour(current_hour)
    if not user_ids:
        return

    for telegram_id in user_ids:
        try:
            token = await models.get_canvas_token(telegram_id)
            if not token:
                continue

            upcoming = await canvas.get_upcoming_assignments(token, days=2)
            if not upcoming:
                continue

            lines = ["Reminder: upcoming deadlines!\n"]
            for a in upcoming:
                course = a.get("_course_name", "Unknown")
                due_dt = a["_due_dt"]
                item_type = a.get("_type", "assignment")
                tag = "[Q] " if item_type == "quiz" else ""
                lines.append(f"- {tag}{a['name']} ({course})")
                lines.append(f"  Due: {due_dt.strftime('%d %b %H:%M')}\n")

            msg = await context.bot.send_message(
                chat_id=telegram_id, text="\n".join(lines)
            )
            # Track as latest bot message for edit-or-reply logic
            chat_data = context.application.chat_data.setdefault(telegram_id, {})
            chat_data["_last_bot_msg_id"] = msg.message_id
        except Exception as e:
            logger.error("Reminder failed for user %s: %s", telegram_id, e)


def main() -> None:
    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # ConversationHandlers (must be added before generic callback handlers)
    app.add_handler(settings.get_setup_handler())
    app.add_handler(notes.get_quicknote_handler())
    app.add_handler(notes.get_search_handler())
    app.add_handler(notes.get_note_handler())
    app.add_handler(todos.get_add_todo_handler())

    # Command handlers
    app.add_handler(CommandHandler("start", start.start))
    app.add_handler(CommandHandler("cancel", start.cancel_cmd))
    app.add_handler(CommandHandler("help", start.help_cmd))
    app.add_handler(CommandHandler("menu", start.menu))
    app.add_handler(CommandHandler("assignments", assignments.assignments_cmd))
    app.add_handler(CommandHandler("due", assignments.due_cmd))
    app.add_handler(CommandHandler("notes", notes.notes_cmd))
    app.add_handler(CommandHandler("unlink", settings.unlink_cmd))
    app.add_handler(CommandHandler("files", files.files_cmd))
    app.add_handler(CommandHandler("reminder", settings.reminder_cmd))
    app.add_handler(CommandHandler("todos", todos.todos_cmd))
    app.add_handler(CommandHandler("add_todo", todos.add_todo_cmd))
    app.add_handler(CommandHandler("refresh", settings.refresh_cmd))

    # Callback query handlers
    app.add_handler(CallbackQueryHandler(start.menu_callback, pattern="^cmd_menu$"))
    app.add_handler(CallbackQueryHandler(start.help_callback, pattern="^cmd_help$"))
    app.add_handler(CallbackQueryHandler(assignments.assignments_callback, pattern="^cmd_assignments$"))
    app.add_handler(CallbackQueryHandler(assignments.due_callback, pattern="^cmd_due$"))
    app.add_handler(CallbackQueryHandler(notes.notes_callback, pattern="^cmd_notes$"))
    app.add_handler(CallbackQueryHandler(settings.settings_callback, pattern="^cmd_settings$"))
    app.add_handler(CallbackQueryHandler(files.files_callback, pattern="^cmd_files$"))
    app.add_handler(CallbackQueryHandler(files.file_course_callback, pattern=r"^fcourse_\d+$"))
    app.add_handler(CallbackQueryHandler(files.folder_callback, pattern=r"^folder_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(settings.unlink_confirm_callback, pattern="^unlink_confirm$"))
    app.add_handler(CallbackQueryHandler(todos.todos_callback, pattern="^cmd_todos$"))
    app.add_handler(CallbackQueryHandler(todos.todos_show_all_callback, pattern=r"^todos_(all|active)$"))
    app.add_handler(CallbackQueryHandler(todos.todo_toggle_callback, pattern=r"^todotoggle_\d+$"))
    app.add_handler(CallbackQueryHandler(todos.todo_delete_callback, pattern=r"^tododel_\d+$"))
    app.add_handler(CallbackQueryHandler(assignments.due_toggle_callback, pattern=r"^due_(show|hide)_submitted$"))
    app.add_handler(CallbackQueryHandler(assignments.course_callback, pattern=r"^course_\d+$"))
    app.add_handler(CallbackQueryHandler(assignments.assignment_detail_callback, pattern=r"^asgn_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(assignments.quiz_detail_callback, pattern=r"^quiz_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(notes.notes_filter_callback, pattern=r"^notes_filter_(assignment|general)$"))
    app.add_handler(CallbackQueryHandler(notes.note_delete, pattern=r"^note_del_\d+_\d+$"))

    # Fallback: unknown messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, notes.unknown_message))
    app.add_handler(MessageHandler(filters.COMMAND, notes.unknown_message))

    # Global error handler
    app.add_error_handler(error_handler)

    # Run reminder check at minute 0 of every hour (SGT-aligned)
    for hour in range(24):
        t = time(hour=hour, minute=0, tzinfo=SGT)
        app.job_queue.run_daily(hourly_reminder, time=t)
    logger.info("Hourly reminder checks scheduled (every hour at :00 SGT)")

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
