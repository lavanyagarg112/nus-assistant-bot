# NUS Assistant Bot

A Telegram bot that helps NUS students manage their Canvas LMS assignments, quizzes, files, and deadlines — all from Telegram.

## Use the Bot

Start chatting with [@nusassistant_bot](https://t.me/nusassistant_bot) on Telegram and run `/setup` to link your Canvas account.

You'll need a **Canvas API token** — generate one from [Canvas](https://canvas.nus.edu.sg) > Account > Settings > New Access Token.

### What it can do

| Command | Description |
|---------|-------------|
| `/assignments` | Browse assignments and quizzes by course, with submission status icons |
| `/due [days]` | View upcoming deadlines split by pending and submitted (default 7 days) |
| `/files` | Browse and open course files |
| `/notes` | View, filter, and search your personal notes |
| `/start_notes` | Capture freeform notes |
| `/todos` | Manage personal to-dos per course |
| `/reminder [hour]` | Set a daily deadline reminder (SGT) |
| `/refresh` | Force a fresh fetch of your course list from Canvas |
| `/help` | Full command list |

**Admin commands** (optional, requires `ADMIN_TELEGRAM_ID` and `ADMIN_PASSWORD` in `.env`):

| Command | Description |
|---------|-------------|
| `/admin <password>` | View user/note/todo counts |
| `/broadcast <password>` | Send a message to all users |

---

## Self-Hosting

Want to run your own instance? Follow the steps below.

### Prerequisites

- Python 3.11+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Access to a Canvas LMS instance

### 1. Clone and install

```bash
git clone https://github.com/lavanyagarg112/nus-assistant-bot.git
cd nus-assistant-bot

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | From [@BotFather](https://t.me/BotFather) |
| `FERNET_KEY` | Encryption key for Canvas tokens (see below) |
| `CANVAS_BASE_URL` | Your Canvas instance URL (default: `https://canvas.nus.edu.sg`) |
| `ADMIN_TELEGRAM_ID` | *(Optional)* Your Telegram user ID for admin commands |
| `ADMIN_PASSWORD` | *(Optional)* Password required for admin commands |

Generate a Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 3. Run

```bash
python main.py
```

The bot will initialise the SQLite database on first run and start polling for messages.

### Project Structure

```
├── main.py                  # Entry point, handler registration, reminder jobs
├── config.py                # Environment variable loading and validation
├── bot/
│   ├── keyboards.py         # Inline keyboard builders
│   └── handlers/
│       ├── start.py         # /start, /help, /menu, /cancel
│       ├── settings.py      # /setup, /unlink, /reminder, /refresh
│       ├── assignments.py   # /assignments, /due, assignment/quiz detail
│       ├── notes.py         # /notes, /start_notes, /end_notes
│       ├── files.py         # /files, folder browsing
│       ├── todos.py         # /todos, /add_todo
│       └── admin.py         # /admin, /broadcast (optional)
├── canvas/
│   └── client.py            # Async Canvas LMS API client
├── db/
│   ├── database.py          # SQLite schema and connection management
│   └── models.py            # CRUD operations
├── requirements.txt
└── .env.example
```

### Key Details

- **Database** — SQLite with WAL mode, stored as `bot.db` by default
- **Token storage** — Canvas API tokens are encrypted at rest using Fernet
- **Token renewal** — If your Canvas token expires, every command will tell you to run `/setup` to add a new one. Running `/setup` again replaces only the token — all your notes, todos, and settings are kept
- **Submission status** — Assignment submission detection uses `submission.attempt` to avoid false positives from instructor-graded items with no actual student submission
- **Item type markers** — `[A]` = Assignment, `[Q]` = Quiz. Shown in course item lists, detail views, and the `/due` deadline list so you can tell them apart at a glance
- **Reminders** — Users set their preferred hour (SGT) via `/reminder` and get a daily push with deadlines due in the next 48 hours. Expired tokens are detected and the user is notified
- **Course cache** — Course list is cached in memory per user and persists until `/refresh` is run, reducing redundant API calls

---

## Feature Requests & Feedback

Have an idea or found something that could be better? Open an issue on [GitHub](https://github.com/lavanyagarg112/nus-assistant-bot/issues) — all suggestions welcome!
