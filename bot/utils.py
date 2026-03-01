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


def make_fallback_command(action_name: str):
    """Create a catch-all fallback handler for a ConversationHandler."""
    async def fallback_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        cmd = update.message.text.split()[0] if update.message.text else "command"
        await reply(
            update.message, context,
            f"/{action_name} was cancelled because you sent {cmd}.\n"
            f"Send {cmd} again, or use /{action_name} to restart.",
        )
        return ConversationHandler.END
    return fallback_command


async def reply_or_edit(query, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs) -> Message:
    """Edit if this callback message is still the latest bot message, otherwise reply fresh."""
    last = context.chat_data.get(_KEY)
    if query.message.message_id == last:
        return await query.edit_message_text(text, **kwargs)
    msg = await query.message.reply_text(text, **kwargs)
    _track(context, msg)
    return msg


async def check_migration_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Block legacy users who linked via Telegram until they re-link via the web flow.

    Returns True if the user is blocked (legacy token), False if OK to proceed.
    """
    from config import WEB_BASE_URL
    if not WEB_BASE_URL:
        return False
    from db.models import get_token_source
    user_id = update.effective_user.id
    source = await get_token_source(user_id)
    if source is not None:
        return False
    text = (
        "Update: Token setup via Telegram is no longer supported. "
        "For your token security, please generate a new token on Canvas "
        "and delete the previous one. Then run /setup to link the new token "
        "— it will go directly to the server over HTTPS, never through Telegram.\n\n"
        "Your notes, todos, and other data will be retained."
    )
    if update.callback_query:
        await reply_or_edit(update.callback_query, context, text)
    elif update.message:
        await reply(update.message, context, text)
    return True
