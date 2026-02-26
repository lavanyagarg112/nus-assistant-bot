import os
import base64
import secrets
import sqlite3

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from azure.identity import DefaultAzureCredential
from azure.keyvault.keys.crypto import CryptographyClient, KeyWrapAlgorithm

DB_PATH = os.environ.get("DB_PATH", "yourdb.sqlite")
FERNET_KEY = os.environ["FERNET_KEY"]
KEYVAULT_KEK_ID = os.environ["KEYVAULT_KEK_ID"]

fernet = Fernet(FERNET_KEY.encode())

crypto = CryptographyClient(
    key=KEYVAULT_KEK_ID,
    credential=DefaultAzureCredential()
)

def ensure_columns(conn: sqlite3.Connection) -> None:
    cols = [
        ("canvas_token_ciphertext_b64", "TEXT"),
        ("canvas_token_nonce_b64", "TEXT"),
        ("canvas_token_wrapped_dek_b64", "TEXT"),
    ]
    for name, typ in cols:
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {typ}")
        except sqlite3.OperationalError as e:
            # "duplicate column name" is fine
            if "duplicate column name" not in str(e).lower():
                raise

def encrypt_new(token: str) -> tuple[str, str, str]:
    dek = secrets.token_bytes(32)     # AES-256
    nonce = secrets.token_bytes(12)   # GCM nonce
    ct = AESGCM(dek).encrypt(nonce, token.encode("utf-8"), None)

    wrapped = crypto.wrap_key(KeyWrapAlgorithm.rsa_oaep, dek).encrypted_key

    return (
        base64.b64encode(ct).decode(),
        base64.b64encode(nonce).decode(),
        base64.b64encode(wrapped).decode(),
    )

def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    ensure_columns(conn)

    # Only migrate rows that don't already have the new fields
    rows = conn.execute(
        """SELECT telegram_id, canvas_token_encrypted
           FROM users
           WHERE canvas_token_encrypted IS NOT NULL
             AND (canvas_token_ciphertext_b64 IS NULL
               OR canvas_token_nonce_b64 IS NULL
               OR canvas_token_wrapped_dek_b64 IS NULL)
        """
    ).fetchall()

    print(f"Found {len(rows)} users to migrate.")

    migrated = 0
    failed = 0

    for telegram_id, legacy_cipher in rows:
        try:
            token = fernet.decrypt(legacy_cipher.encode()).decode()
            ct_b64, nonce_b64, wrapped_b64 = encrypt_new(token)

            conn.execute(
                """UPDATE users
                   SET canvas_token_ciphertext_b64 = ?,
                       canvas_token_nonce_b64 = ?,
                       canvas_token_wrapped_dek_b64 = ?
                   WHERE telegram_id = ?""",
                (ct_b64, nonce_b64, wrapped_b64, telegram_id)
            )
            migrated += 1
        except Exception as e:
            failed += 1
            # Don't print token. Only print user id + error summary.
            print(f"[FAIL] telegram_id={telegram_id} err={type(e).__name__}: {e}")

    conn.commit()
    conn.close()

    print(f"Done. migrated={migrated} failed={failed}")

if __name__ == "__main__":
    main()