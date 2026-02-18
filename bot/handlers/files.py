import logging

from telegram import Update
from telegram.ext import ContextTypes

import config
from bot import keyboards
from bot.handlers.assignments import _escape_md, _escape_url, _require_token
from canvas import client as canvas

logger = logging.getLogger(__name__)


# ── /files command ──


async def files_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    token = await _require_token(update)
    if not token:
        return
    msg = update.message
    loading = await msg.reply_text("Loading courses...")
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
        "Select a course to browse files:",
        reply_markup=keyboards.file_course_list(courses),
    )


async def files_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle cmd_files from menu."""
    query = update.callback_query
    await query.answer()
    token = await _require_token(update)
    if not token:
        return
    try:
        courses = await canvas.get_courses(token)
    except Exception:
        logger.error("Canvas API error fetching courses for user %s", update.effective_user.id)
        await query.edit_message_text("Failed to fetch courses.")
        return

    if not courses:
        await query.edit_message_text("No active courses found.")
        return

    await query.edit_message_text(
        "Select a course to browse files:",
        reply_markup=keyboards.file_course_list(courses),
    )


# ── Course files root callback ──


async def file_course_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    token = await _require_token(update)
    if not token:
        return

    course_id = int(query.data.split("_")[1])
    await query.edit_message_text("Loading files...")

    try:
        root = await canvas.get_root_folder(token, course_id)
    except Exception:
        logger.error("Canvas API error fetching root folder for user %s", update.effective_user.id)
        await query.edit_message_text("Failed to load files for this course.")
        return

    if not root:
        await query.edit_message_text(
            "No files found.", reply_markup=keyboards.back_to_menu()
        )
        return

    await _show_folder(query, token, root["id"], course_id)


# ── Folder navigation callback ──


async def folder_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    token = await _require_token(update)
    if not token:
        return

    parts = query.data.split("_")
    folder_id = int(parts[1])
    course_id = int(parts[2])

    await _show_folder(query, token, folder_id, course_id)


async def _show_folder(query, token: str, folder_id: int, course_id: int) -> None:
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
            "This folder is empty.",
            reply_markup=keyboards.file_back(course_id),
        )
        return

    # Build text with file links
    lines = []
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

    text = "\n".join(lines) if lines else "No files in this folder\\."

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
