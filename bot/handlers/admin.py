import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, filters

import config
from bot.utils import fallback_command, reply
from db import models

logger = logging.getLogger(__name__)

WAITING_BROADCAST = 0


_UNKNOWN = "I didn't understand that. Type /help to see available commands."


def _check_admin(user_id: int, args: list[str]) -> bool:
    if not config.ADMIN_TELEGRAM_ID or user_id != config.ADMIN_TELEGRAM_ID:
        return False
    if not config.ADMIN_PASSWORD:
        return False
    return len(args) >= 1 and args[0] == config.ADMIN_PASSWORD


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_admin(update.effective_user.id, context.args or []):
        await reply(update.message, context, _UNKNOWN)
        return

    stats = await models.get_stats()
    text = (
        "Admin Dashboard\n\n"
        f"Users: {stats['users']}\n"
        f"Reminders enabled: {stats['reminders_enabled']}\n\n"
        f"Assignment notes: {stats['notes']}\n"
        f"General notes: {stats['general_notes']}\n"
        f"Todos: {stats['todos']} ({stats['todos_done']} done)\n"
    )
    await reply(update.message, context, text)


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _check_admin(update.effective_user.id, context.args or []):
        await reply(update.message, context, _UNKNOWN)
        return ConversationHandler.END

    await reply(update.message, context, "Send me the broadcast message (or /cancel):")
    return WAITING_BROADCAST


async def broadcast_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text:
        await reply(update.message, context, "Message can't be empty. Try again or /cancel.")
        return WAITING_BROADCAST

    user_ids = await models.get_all_user_ids()
    status = await reply(update.message, context, f"Sending to {len(user_ids)} users...")

    sent = 0
    failed = 0

    async def _send(tid: int) -> bool:
        try:
            await context.bot.send_message(chat_id=tid, text=text)
            return True
        except Exception:
            return False

    results = await asyncio.gather(*[_send(tid) for tid in user_ids])
    for success in results:
        if success:
            sent += 1
        else:
            failed += 1

    await status.edit_text(f"Broadcast complete: {sent} sent, {failed} failed.")
    return ConversationHandler.END


async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await reply(update.message, context, "Broadcast cancelled.")
    return ConversationHandler.END


def get_broadcast_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_cmd)],
        states={
            WAITING_BROADCAST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_receive),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", broadcast_cancel),
            MessageHandler(filters.COMMAND, fallback_command),
        ],
        per_message=False,
    )
