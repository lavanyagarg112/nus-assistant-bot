import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
)

import config
from bot import keyboards
from bot.utils import check_migration_reminder, reply, reply_or_edit
from canvas import client as canvas
from db import models

logger = logging.getLogger(__name__)


async def setup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        await reply(
            update.message, context,
            "For security, /setup can only be used in a private chat with me."
        )
        return

    telegram_id = update.effective_user.id
    existing_token = await models.get_canvas_token(telegram_id)

    if existing_token:
        prompt = (
            "You already have a Canvas account linked.\n"
            "You can use the link below to update your token anytime — "
            "whether it has expired or you just want to replace it. "
            "Your notes, todos, and other data will be kept.\n\n"
        )
    else:
        prompt = "Let's link your Canvas account.\n\n"

    instructions = (
        "To get your Canvas API token:\n"
        "1. Go to https://canvas.nus.edu.sg\n"
        "2. Click your profile icon -> Settings\n"
        "3. Scroll to Approved Integrations -> + New Access Token\n"
        "4. Set an expiry date for the token (recommended for security)\n"
        "5. Copy the token\n\n"
        "Security tips:\n"
        "- Your token is stored encrypted — never in plain text\n"
        "- Always set a token expiry — avoid tokens that never expire\n"
        "- You can update your token anytime by running /setup again\n"
        "- Use /unlink to remove your token and all data at any time\n"
    )

    if config.WEB_BASE_URL:
        from web.server import generate_otp
        otp = generate_otp(telegram_id)
        link = f"{config.WEB_BASE_URL}/link?token={otp}"
        await reply(
            update.message, context,
            f"{prompt}{instructions}\n"
            f"Open this link to paste your token securely (expires in 5 min):\n{link}\n\n"
            "Your token never passes through Telegram — it goes directly from your browser to the bot server over HTTPS."
        )
    else:
        await reply(
            update.message, context,
            f"{prompt}{instructions}\n"
            "Web-based token setup is not configured. Please contact the bot administrator."
        )


async def unlink_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    existing_token = await models.get_canvas_token(telegram_id)
    if not existing_token:
        await reply(update.message, context, "No Canvas account is linked.")
        return
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Yes, delete everything", callback_data="unlink_confirm"),
            InlineKeyboardButton("Cancel", callback_data="cmd_menu"),
        ]
    ])
    await reply(
        update.message, context,
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
    await reply_or_edit(
        query, context,
        "All your data has been deleted.\n"
        "Run /setup to link a new Canvas account."
    )


async def reminder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    existing = await models.get_reminder_hour(telegram_id)
    if existing is None:
        await reply(update.message, context, "You need to /setup first.")
        return

    if await check_migration_reminder(update, context):
        return

    if not context.args:
        await reply(
            update.message, context,
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
        await reply(update.message, context, "Please provide a valid hour (0-23). Example: /reminder 8")
        return

    await models.set_reminder_hour(telegram_id, hour)
    await reply(update.message, context, f"Reminder time updated to {hour}:00 SGT.")


async def refresh_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    token = await models.get_canvas_token(telegram_id)
    if not token:
        await reply(update.message, context, "You need to /setup first.")
        return
    if await check_migration_reminder(update, context):
        return
    canvas.clear_course_cache(token)
    await reply(update.message, context, "Course cache cleared. Your next request will fetch fresh data from Canvas.")


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    telegram_id = update.effective_user.id
    registered = await models.is_registered(telegram_id)

    if registered:
        if await check_migration_reminder(update, context):
            return
        reminder_hour = await models.get_reminder_hour(telegram_id)
        reminder_str = f"{reminder_hour}:00 SGT" if reminder_hour is not None else "Not set"
        text = (
            "⚙️ Settings\n\n"
            f"Canvas account: Linked\n"
            f"Daily reminder: {reminder_str}\n\n"
            "/unlink — Remove your Canvas account\n"
            "/reminder <hour> — Change reminder time (0-23)\n"
            "/refresh — Refresh cached course list"
        )
    else:
        text = (
            "⚙️ Settings\n\n"
            "Canvas account: Not linked\n\n"
            "/setup — Link your Canvas account"
        )

    await reply_or_edit(query, context, text, reply_markup=keyboards.back_to_menu())


def get_setup_handler() -> CommandHandler:
    return CommandHandler("setup", setup_cmd)
