from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from canvas.client import is_submitted


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Assignments", callback_data="cmd_assignments"),
                InlineKeyboardButton("Due Soon", callback_data="cmd_due"),
            ],
            [
                InlineKeyboardButton("My Notes", callback_data="cmd_notes"),
                InlineKeyboardButton("TODOs", callback_data="cmd_todos"),
            ],
            [
                InlineKeyboardButton("Files", callback_data="cmd_files"),
            ],
            [
                InlineKeyboardButton("Settings", callback_data="cmd_settings"),
                InlineKeyboardButton("Help", callback_data="cmd_help"),
            ],
        ]
    )


def course_list(courses: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(c["name"][:40], callback_data=f"course_{c['id']}")]
        for c in courses
    ]
    buttons.append([InlineKeyboardButton("<< Back to Menu", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(buttons)


def assignment_list(assignments: list[dict], course_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                a["name"][:40],
                callback_data=f"asgn_{course_id}_{a['id']}",
            )
        ]
        for a in assignments[:20]  # Limit to 20 to avoid Telegram limits
    ]
    buttons.append(
        [InlineKeyboardButton("<< Back to Courses", callback_data="cmd_assignments")]
    )
    return InlineKeyboardMarkup(buttons)


def course_items_list(
    assignments: list[dict], quizzes: list[dict], course_id: int
) -> InlineKeyboardMarkup:
    buttons = []
    for a in assignments[:15]:
        icon = "\u2705" if is_submitted(a) else "\u2b1c"
        buttons.append([InlineKeyboardButton(
            f"{icon} [A] {a['name'][:33]}",
            callback_data=f"asgn_{course_id}_{a['id']}",
        )])
    for q in quizzes[:10]:
        q_item = {**q, "_type": "quiz"}
        icon = "\u2705" if is_submitted(q_item) else "\u2b1c"
        buttons.append([InlineKeyboardButton(
            f"{icon} [Q] {q.get('title', 'Quiz')[:33]}",
            callback_data=f"quiz_{course_id}_{q['id']}",
        )])
    buttons.append(
        [InlineKeyboardButton("<< Back to Courses", callback_data="cmd_assignments")]
    )
    return InlineKeyboardMarkup(buttons)


def assignment_detail(course_id: int, assignment_id: int, has_note: bool) -> InlineKeyboardMarkup:
    note_label = "Edit Note" if has_note else "Add Note"
    buttons = [
        [InlineKeyboardButton(note_label, callback_data=f"note_add_{course_id}_{assignment_id}")],
    ]
    if has_note:
        buttons.append(
            [InlineKeyboardButton("Delete Note", callback_data=f"note_del_{course_id}_{assignment_id}")]
        )
    buttons.append(
        [InlineKeyboardButton("<< Back to Assignments", callback_data=f"course_{course_id}")]
    )
    return InlineKeyboardMarkup(buttons)


def quiz_detail(course_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("<< Back to Course", callback_data=f"course_{course_id}")],
    ])


def file_course_list(courses: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(c["name"][:40], callback_data=f"fcourse_{c['id']}")]
        for c in courses
    ]
    buttons.append([InlineKeyboardButton("<< Back to Menu", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(buttons)


def folder_contents(subfolders: list[dict], course_id: int) -> InlineKeyboardMarkup:
    buttons = []
    for f in subfolders[:15]:
        name = f.get("name", "folder")[:37]
        buttons.append([InlineKeyboardButton(
            f"📁 {name}",
            callback_data=f"folder_{f['id']}_{course_id}",
        )])
    buttons.append([InlineKeyboardButton("<< Back to Courses", callback_data="cmd_files")])
    return InlineKeyboardMarkup(buttons)


def file_back(course_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("<< Back to Courses", callback_data="cmd_files")],
    ])


def notes_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Assignment Notes", callback_data="notes_filter_assignment"),
            InlineKeyboardButton("General Notes", callback_data="notes_filter_general"),
        ],
        [
            InlineKeyboardButton("Search Notes", callback_data="notes_search"),
        ],
        [InlineKeyboardButton("<< Back to Menu", callback_data="cmd_menu")],
    ])


def back_to_notes() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("<< Back to Notes", callback_data="cmd_notes")],
        [InlineKeyboardButton("<< Back to Menu", callback_data="cmd_menu")],
    ])


def due_list(show_submitted: bool, days: int) -> InlineKeyboardMarkup:
    if show_submitted:
        toggle = InlineKeyboardButton("Hide Submitted", callback_data=f"due_hide_submitted_{days}")
    else:
        toggle = InlineKeyboardButton("Show Submitted", callback_data=f"due_show_submitted_{days}")
    return InlineKeyboardMarkup([
        [toggle],
        [InlineKeyboardButton("<< Back to Menu", callback_data="cmd_menu")],
    ])


def back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("<< Back to Menu", callback_data="cmd_menu")]]
    )
