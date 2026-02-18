import os
import sys

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
FERNET_KEY = os.environ["FERNET_KEY"]
CANVAS_BASE_URL = os.getenv("CANVAS_BASE_URL", "https://canvas.nus.edu.sg").rstrip("/")
DB_PATH = os.getenv("DB_PATH", "bot.db")

# ── Startup validations ──

if not CANVAS_BASE_URL.startswith("https://"):
    print("ERROR: CANVAS_BASE_URL must start with https://", file=sys.stderr)
    sys.exit(1)

try:
    from cryptography.fernet import Fernet
    Fernet(FERNET_KEY.encode())
except Exception:
    print("ERROR: FERNET_KEY is invalid. Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"", file=sys.stderr)
    sys.exit(1)
