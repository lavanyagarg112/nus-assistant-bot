# NUS Assistant Bot

A Telegram bot that helps NUS students manage their Canvas LMS assignments, quizzes, files, and deadlines — all from Telegram. Canvas API tokens are encrypted at rest using Azure Key Vault envelope encryption.

## Use the Bot

Start chatting with [@nusassistant_bot](https://t.me/nusassistant_bot) on Telegram and run `/setup` to link your Canvas account.

You'll need a **Canvas API token** — generate one from [Canvas](https://canvas.nus.edu.sg) > Account > Settings > New Access Token. The bot will give you a secure link to paste your token — it goes directly to the server over HTTPS, never through Telegram.

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
| `FERNET_KEY` | Encryption key for notes, todos, and tokens (see below) |
| `CANVAS_BASE_URL` | Your Canvas instance URL (default: `https://canvas.nus.edu.sg`) |
| `ADMIN_TELEGRAM_ID` | *(Optional)* Your Telegram user ID for admin commands |
| `ADMIN_PASSWORD` | *(Optional)* Password required for admin commands |
| `KEYVAULT_KEK_ID` | *(Optional)* Azure Key Vault key URI for token encryption (see [Azure Key Vault](#azure-key-vault-optional)) |
| `IS_SELF_HOSTED` | Set to `True` for self-hosted deployments (default: `False`) |
| `CANVAS_TOKEN` | *(Self-hosted only)* Your Canvas API token — `/setup` will use this directly |
| `WEB_BASE_URL` | *(Production only)* Public URL for the web-based token setup page (e.g. `https://yourbot.example.com`) |
| `WEB_PORT` | *(Production only)* Port for the web server (default: `8080`). Runs behind a reverse proxy (nginx) |

Generate a Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

That's it — `FERNET_KEY` is the only encryption key you need for local use. All data (tokens, notes, todos) will be encrypted with Fernet.

For self-hosted use, set `IS_SELF_HOSTED=True` and `CANVAS_TOKEN` to your Canvas API token. Run `/setup` in the bot and it will link your token directly from the `.env` — no web server needed.

### 3. Run

```bash
python main.py
```

The bot will initialise the SQLite database on first run and start polling for messages.

### Project Structure

```
├── main.py                  # Entry point, handler registration, reminder jobs
├── config.py                # Environment variable loading and validation
├── azure_migration.py       # One-time migration: Fernet tokens → Azure Key Vault
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
├── web/
│   └── server.py            # aiohttp web server for secure token setup
├── canvas/
│   └── client.py            # Async Canvas LMS API client
├── db/
│   ├── database.py          # SQLite schema and connection management
│   └── models.py            # CRUD operations
├── requirements.txt
└── .env.example
```

### Azure Key Vault (Optional)

For production deployments, you can use Azure Key Vault to protect Canvas API tokens with envelope encryption. This is optional — without it, tokens are encrypted with Fernet.

1. Create an RSA key in your Azure Key Vault
2. Grant the VM's managed identity the **Key Vault Crypto User** role
3. Set `KEYVAULT_KEK_ID` in `.env` to the full key URI (e.g. `https://<vault>.vault.azure.net/keys/<key-name>/<version>`)
4. Run `python azure_migration.py` to migrate existing Fernet-encrypted tokens to the new format
5. Restart the bot

When `KEYVAULT_KEK_ID` is set, new tokens are encrypted via Key Vault. Notes and todos remain Fernet-encrypted. If `KEYVAULT_KEK_ID` is not set, everything uses Fernet as before.

### Key Details

- **Database** — SQLite with WAL mode, stored as `bot.db` by default
- **Encryption** — Canvas API tokens are encrypted at rest using either Fernet (default) or Azure Key Vault envelope encryption (if configured). Notes and todos are always Fernet-encrypted
- **Token setup** — In production (`WEB_BASE_URL` set), tokens are linked via a web page served over HTTPS, so they never pass through Telegram. `/setup` generates a single-use link (expires in 5 minutes) to a token submission form. Requires a reverse proxy (e.g. nginx) for TLS termination. For self-hosted use (`IS_SELF_HOSTED=True`), the token is read directly from `CANVAS_TOKEN` in `.env`
- **Token renewal** — If your Canvas token expires, every command will tell you to run `/setup` to add a new one. Running `/setup` again replaces only the token — all your notes, todos, and settings are kept
- **Timezones** — All times are displayed in SGT (UTC+8)
- **Submission status** — Assignment submission detection uses `submission.attempt` to avoid false positives from instructor-graded items with no actual student submission
- **Item type markers** — `[A]` = Assignment, `[Q]` = Quiz. Shown in course item lists, detail views, and the `/due` deadline list so you can tell them apart at a glance
- **Reminders** — Users set their preferred hour (SGT) via `/reminder` and get a daily push with deadlines due in the next 48 hours. Expired tokens are detected and the user is notified
- **Course cache** — Course list is cached in memory per user and persists until `/refresh` is run, reducing redundant API calls

---

## Feature Requests & Feedback

Have an idea or found something that could be better? Open an issue on [GitHub](https://github.com/lavanyagarg112/nus-assistant-bot/issues) — all suggestions welcome!
