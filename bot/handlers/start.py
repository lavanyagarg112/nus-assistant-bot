from telegram import Update
from telegram.ext import ContextTypes

from bot import keyboards
from bot.utils import reply, reply_or_edit

WELCOME_MSG = (
    "Welcome to NUS Assignment Bot!\n\n"
    "I help you track your Canvas LMS assignments and add personal notes.\n\n"
    "To get started, run /setup to link your Canvas account.\n"
    "Then use /menu to browse your assignments."
)

HELP_MSG = (
    "📚 *Assignments & Deadlines*\n"
    "/assignments — Browse by course \\(includes quizzes \\[Q\\], assignments \\[A\\]\\)\n"
    "/due \\[days\\] — Upcoming deadlines \\(default 7, e\\.g\\. /due 14\\)\n"
    "\n"
    "📁 *Files*\n"
    "/files — Browse course files and folders\n"
    "\n"
    "📝 *Notes*\n"
    "/notes — View all notes \\(filter by type, search\\)\n"
    "/start\\_notes \\(/sn\\) — Start freeform capture mode\n"
    "/end\\_notes — Save and exit capture mode\n"
    "\n"
    "✅ *TODOs*\n"
    "/todos — View active todos\n"
    "/todos all — Include completed todos\n"
    "/add\\_todo — Add a todo \\(per course or general\\)\n"
    "\n"
    "📅 *Events*\n"
    "/events — View your custom exams and assignments\n"
    "/add\\_event — Add a custom exam or assignment\n"
    "\n"
    "⚙️ *Settings*\n"
    "/setup — Link your Canvas account\n"
    "/unlink — Remove your Canvas account\n"
    "/reminder \\[hour\\] — Set daily reminder time \\(SGT\\)\n"
    "  _Sends a push at that hour with deadlines due in the next 48h\\. Default: 9:00 AM_\n"
    "/refresh — Refresh cached course list from Canvas\n"
    "\n"
    "🔧 *General*\n"
    "/menu — Main menu\n"
    "/cancel — Cancel the current action\n"
    "/help — This help message"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update.message, context, WELCOME_MSG, reply_markup=keyboards.main_menu())


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global /cancel for when there's nothing active to cancel."""
    await reply(
        update.message, context,
        "Nothing to cancel. Use /menu to see your options.",
        reply_markup=keyboards.main_menu(),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update.message, context, HELP_MSG, parse_mode="MarkdownV2", reply_markup=keyboards.back_to_menu())


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update.message, context, "Main Menu:", reply_markup=keyboards.main_menu())


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await reply_or_edit(query, context, "Main Menu:", reply_markup=keyboards.main_menu())


async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await reply_or_edit(query, context, HELP_MSG, parse_mode="MarkdownV2", reply_markup=keyboards.back_to_menu())
