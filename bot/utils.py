"""Helpers for consistent message-sending behaviour.

We track the last bot message per chat so that callback handlers can decide
whether to edit in place (message is still the latest) or reply fresh
(user has sent messages since, so the button message is scrolled up).
"""

from telegram import Message, Update
from telegram.ext import ContextTypes, ConversationHandler

_KEY = "_last_bot_msg_id"


def _track(context: ContextTypes.DEFAULT_TYPE, message: Message) -> None:
    context.chat_data[_KEY] = message.message_id


async def reply(target: Message, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs) -> Message:
    """Send reply_text and track as the latest bot message."""
    msg = await target.reply_text(text, **kwargs)
    _track(context, msg)
    return msg


async def send(chat, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs) -> Message:
    """Send via Chat.send_message and track as the latest bot message."""
    msg = await chat.send_message(text, **kwargs)
    _track(context, msg)
    return msg


def breadcrumb(*parts: str) -> str:
    """Build a navigation path like 'Assignments > CS2030S > Homework 1'."""
    return " > ".join(parts)


async def fallback_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Catch-all fallback for ConversationHandlers: cancel and tell user to retry."""
    await reply(update.message, context, "Previous action cancelled. Please re-send your command.")
    return ConversationHandler.END


async def reply_or_edit(query, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs) -> Message:
    """Edit if this callback message is still the latest bot message, otherwise reply fresh."""
    last = context.chat_data.get(_KEY)
    if query.message.message_id == last:
        return await query.edit_message_text(text, **kwargs)
    msg = await query.message.reply_text(text, **kwargs)
    _track(context, msg)
    return msg
