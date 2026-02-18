import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot import keyboards
from canvas import client as canvas
from db import models

logger = logging.getLogger(__name__)

WAITING_TOKEN = 0


async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_chat.type != "private":
        await update.message.reply_text(
            "For security, /setup can only be used in a private chat with me."
        )
        return ConversationHandler.END

    telegram_id = update.effective_user.id
    existing_token = await models.get_canvas_token(telegram_id)
    if existing_token:
        await update.message.reply_text(
            "You already have a Canvas account linked.\n"
            "Use /unlink to remove it first if you want to use a different token."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "Let's link your Canvas account.\n\n"
        "To get your Canvas API token:\n"
        "1. Go to https://canvas.nus.edu.sg\n"
        "2. Click your profile icon -> Settings\n"
        "3. Scroll to Approved Integrations -> + New Access Token\n"
        "4. Copy the token\n\n"
        "Please paste your Canvas API token now.\n"
        "(Your message will be deleted for security)\n\n"
        "Send /cancel to abort."
    )
    return WAITING_TOKEN


async def setup_receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    token = update.message.text.strip()
    telegram_id = update.effective_user.id

    # Delete the message containing the token for security
    try:
        await update.message.delete()
    except Exception:
        logger.warning("Could not delete token message for user %s", telegram_id)

    if not token or len(token) < 10:
        await update.effective_chat.send_message(
            "That doesn't look like a valid token. Please try /setup again."
        )
        return ConversationHandler.END

    # Validate the token by making a test API call
    await update.effective_chat.send_message("Verifying your token...")
    try:
        courses = await canvas.get_courses(token)
    except Exception as e:
        logger.warning("Token validation failed for user %s: %s", telegram_id, e)
        await update.effective_chat.send_message(
            "That token doesn't seem to work. Please check it and try /setup again."
        )
        return ConversationHandler.END

    await models.upsert_user(telegram_id, token)
    await update.effective_chat.send_message(
        f"Canvas token verified and saved! Found {len(courses)} active course(s).\n\n"
        "Try /assignments or /due to see your assignments."
    )
    return ConversationHandler.END


async def setup_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Setup cancelled.")
    return ConversationHandler.END


async def unlink_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    existing_token = await models.get_canvas_token(telegram_id)
    if not existing_token:
        await update.message.reply_text("No Canvas account is linked.")
        return
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Yes, delete everything", callback_data="unlink_confirm"),
            InlineKeyboardButton("Cancel", callback_data="cmd_menu"),
        ]
    ])
    await update.message.reply_text(
        "Are you sure? This will permanently delete:\n"
        "- Your Canvas token\n"
        "- All your assignment notes\n"
        "- All your general notes\n"
        "- All your todos\n\n"
        "This cannot be undone.",
        reply_markup=keyboard,
    )


async def unlink_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    telegram_id = update.effective_user.id
    await models.delete_user(telegram_id)
    await query.edit_message_text(
        "All your data has been deleted.\n"
        "Run /setup to link a new Canvas account."
    )


async def reminder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    existing = await models.get_reminder_hour(telegram_id)
    if existing is None:
        await update.message.reply_text("You need to /setup first.")
        return

    if not context.args:
        await update.message.reply_text(
            f"Your daily reminder is set to {existing}:00 SGT.\n\n"
            "To change it, use /reminder <hour> (0-23).\n"
            "Example: /reminder 8 for 8:00 AM SGT"
        )
        return

    try:
        hour = int(context.args[0])
        if not 0 <= hour <= 23:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Please provide a valid hour (0-23). Example: /reminder 8")
        return

    await models.set_reminder_hour(telegram_id, hour)
    await update.message.reply_text(f"Reminder time updated to {hour}:00 SGT.")


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    registered = await models.is_registered(update.effective_user.id)
    if registered:
        await query.edit_message_text(
            "Your Canvas account is linked.\n"
            "Run /unlink to remove it.",
            reply_markup=keyboards.back_to_menu(),
        )
    else:
        await query.edit_message_text(
            "No Canvas account linked yet.\n"
            "Run /setup to get started.",
            reply_markup=keyboards.back_to_menu(),
        )


def get_setup_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("setup", setup_start)],
        states={
            WAITING_TOKEN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, setup_receive_token)
            ],
        },
        fallbacks=[CommandHandler("cancel", setup_cancel)],
    )
