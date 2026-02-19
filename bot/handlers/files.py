import logging

from telegram import Update
from telegram.ext import ContextTypes

import config
from bot import keyboards
from bot.handlers.assignments import (
    _course_name,
    _escape_md,
    _escape_url,
    _require_token,
)
from bot.utils import breadcrumb, reply, reply_or_edit
from canvas import client as canvas

logger = logging.getLogger(__name__)


# ── /files command ──


async def files_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    token = await _require_token(update, context)
    if not token:
        return
    msg = update.message
    loading = await reply(msg, context, "Loading courses...")
    try:
        courses = await canvas.get_courses(token)
    except Exception:
        logger.error("Canvas API error fetching courses for user %s", update.effective_user.id)
        await loading.edit_text("Failed to fetch courses.")
        return

    if not courses:
        await loading.edit_text("No active courses found.")
        return

    await loading.edit_text(
        "Files\n\nSelect a course:",
        reply_markup=keyboards.file_course_list(courses),
    )


async def files_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle cmd_files from menu."""
    query = update.callback_query
    await query.answer()
    token = await _require_token(update, context)
    if not token:
        return
    try:
        courses = await canvas.get_courses(token)
    except Exception:
        logger.error("Canvas API error fetching courses for user %s", update.effective_user.id)
        await reply_or_edit(query, context, "Failed to fetch courses.")
        return

    if not courses:
        await reply_or_edit(query, context, "No active courses found.")
        return

    await reply_or_edit(
        query, context,
        "Files\n\nSelect a course:",
        reply_markup=keyboards.file_course_list(courses),
    )


# ── Course files root callback ──


async def file_course_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    token = await _require_token(update, context)
    if not token:
        return

    course_id = int(query.data.split("_")[1])
    await query.edit_message_text("Loading files...")

    course_name = await _course_name(token, course_id)
    path = breadcrumb("Files", course_name) if course_name else "Files"

    try:
        root = await canvas.get_root_folder(token, course_id)
    except Exception:
        logger.error("Canvas API error fetching root folder for user %s", update.effective_user.id)
        await query.edit_message_text("Failed to load files for this course.")
        return

    if not root:
        await query.edit_message_text(
            f"{path}\n\nNo files found.", reply_markup=keyboards.back_to_menu()
        )
        return

    await _show_folder(query, token, root["id"], course_id, path)


# ── Folder navigation callback ──


async def folder_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    token = await _require_token(update, context)
    if not token:
        return

    parts = query.data.split("_")
    folder_id = int(parts[1])
    course_id = int(parts[2])

    # Get the folder name from the clicked button
    folder_name = None
    if query.message and query.message.reply_markup:
        for row in query.message.reply_markup.inline_keyboard:
            for btn in row:
                if btn.callback_data == query.data:
                    folder_name = btn.text.removeprefix("📁 ")
                    break

    # Build path from the previous message's first line (preserve breadcrumb)
    prev_path = None
    if query.message and query.message.text:
        prev_path = query.message.text.split("\n")[0]

    if prev_path and folder_name:
        path = breadcrumb(prev_path, folder_name)
    elif prev_path:
        path = prev_path
    else:
        course_name = await _course_name(token, course_id)
        path = breadcrumb("Files", course_name) if course_name else "Files"

    await _show_folder(query, token, folder_id, course_id, path)


async def _show_folder(query, token: str, folder_id: int, course_id: int, path: str = "Files") -> None:
    """Display folder contents (subfolders + files)."""
    try:
        subfolders = await canvas.get_subfolders(token, folder_id)
        files = await canvas.get_folder_files(token, folder_id)
    except Exception:
        logger.error("Canvas API error fetching folder %s", folder_id)
        await query.edit_message_text("Failed to load folder contents.")
        return

    if not subfolders and not files:
        await query.edit_message_text(
            f"{_escape_md(path)}\n\nThis folder is empty\\.",
            parse_mode="MarkdownV2",
            reply_markup=keyboards.file_back(course_id),
        )
        return

    # Build text with file links
    lines = [f"{_escape_md(path)}\n"]
    if files:
        for f in files[:15]:
            name = f.get("display_name", "file")
            size = f.get("size", 0)
            size_str = _format_size(size)
            file_id = f.get("id", "")
            url = f"{config.CANVAS_BASE_URL}/courses/{course_id}/files/{file_id}" if file_id else ""
            if url:
                lines.append(f"[{_escape_md(name)}]({_escape_url(url)}) \\({_escape_md(size_str)}\\)")
            else:
                lines.append(f"{_escape_md(name)} \\({_escape_md(size_str)}\\)")

    if not files:
        lines.append("No files in this folder\\.")

    text = "\n".join(lines)

    await query.edit_message_text(
        text,
        parse_mode="MarkdownV2",
        reply_markup=keyboards.folder_contents(subfolders, course_id),
        disable_web_page_preview=True,
    )


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"
