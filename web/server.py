import html as html_mod
import logging
import secrets
import time as _time

from aiohttp import web

from canvas import client as canvas
from db import models

logger = logging.getLogger(__name__)

OTP_TTL = 300  # 5 minutes

# {otp_string: (telegram_id, expiry_timestamp)}
_otp_store: dict[str, tuple[int, float]] = {}


def generate_otp(telegram_id: int) -> str:
    """Create a single-use OTP for the given user, invalidating any prior OTP."""
    # Invalidate existing OTPs for this user
    to_remove = [k for k, (tid, _) in _otp_store.items() if tid == telegram_id]
    for k in to_remove:
        del _otp_store[k]

    otp = secrets.token_urlsafe(32)
    _otp_store[otp] = (telegram_id, _time.time() + OTP_TTL)
    return otp


def _consume_otp(otp: str) -> int | None:
    """Validate and consume an OTP. Returns telegram_id or None."""
    entry = _otp_store.get(otp)
    if entry is None:
        return None
    tid, expiry = entry
    del _otp_store[otp]
    if _time.time() > expiry:
        return None
    return tid


# ── HTML templates ──

_LINK_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Link Canvas Account</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #f5f5f5; color: #333; display: flex; justify-content: center;
         align-items: center; min-height: 100vh; padding: 16px; }
  .card { background: #fff; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.1);
          max-width: 440px; width: 100%%; padding: 32px; }
  h1 { font-size: 1.4rem; margin-bottom: 8px; }
  .desc { color: #666; font-size: .9rem; margin-bottom: 20px; line-height: 1.5; }
  label { font-weight: 600; font-size: .9rem; display: block; margin-bottom: 6px; }
  input[type=text] { width: 100%%; padding: 10px 12px; border: 1px solid #ccc;
                     border-radius: 8px; font-size: 1rem; margin-bottom: 16px; }
  input[type=text]:focus { outline: none; border-color: #4a90d9; box-shadow: 0 0 0 3px rgba(74,144,217,.2); }
  button { width: 100%%; padding: 12px; background: #4a90d9; color: #fff; border: none;
           border-radius: 8px; font-size: 1rem; font-weight: 600; cursor: pointer; }
  button:hover { background: #3a7bc8; }
  button:disabled { background: #aaa; cursor: not-allowed; }
  .note { margin-top: 16px; font-size: .8rem; color: #888; line-height: 1.4; }
  .msg { margin-top: 12px; padding: 10px; border-radius: 8px; font-size: .9rem; display: none; }
  .msg.ok { display: block; background: #e6f4ea; color: #1e7e34; }
  .msg.err { display: block; background: #fdecea; color: #c62828; }
</style>
</head>
<body>
<div class="card">
  <h1>Link Your Canvas Account</h1>
  <p class="desc">Paste your Canvas API token below. It will be sent directly to the bot server over HTTPS — never through Telegram.</p>
  <form id="f" onsubmit="return handleSubmit(event)">
    <input type="hidden" name="otp" value="%(otp)s">
    <label for="token">Canvas API Token</label>
    <input type="text" id="token" name="token" autocomplete="off" placeholder="Paste your token here" required>
    <button type="submit" id="btn">Link Account</button>
  </form>
  <div id="msg" class="msg"></div>
  <p class="note">Your token is encrypted at rest and used only to fetch your Canvas data. You can revoke it anytime from Canvas settings or by running /unlink in the bot.</p>
</div>
<script>
async function handleSubmit(e) {
  e.preventDefault();
  const btn = document.getElementById('btn');
  const msg = document.getElementById('msg');
  btn.disabled = true;
  btn.textContent = 'Verifying...';
  msg.className = 'msg';
  msg.style.display = 'none';
  try {
    const fd = new FormData(document.getElementById('f'));
    const res = await fetch('/link/submit', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.ok) {
      msg.className = 'msg ok';
      msg.textContent = data.message;
      btn.textContent = 'Linked!';
    } else {
      msg.className = 'msg err';
      msg.textContent = data.message;
      btn.disabled = false;
      btn.textContent = 'Link Account';
    }
  } catch {
    msg.className = 'msg err';
    msg.textContent = 'Network error. Please try again.';
    btn.disabled = false;
    btn.textContent = 'Link Account';
  }
}
</script>
</body>
</html>
"""

_EXPIRED_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Link Expired</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #f5f5f5; color: #333; display: flex; justify-content: center;
         align-items: center; min-height: 100vh; padding: 16px; }
  .card { background: #fff; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.1);
          max-width: 440px; width: 100%; padding: 32px; text-align: center; }
  h1 { font-size: 1.4rem; margin-bottom: 12px; }
  p { color: #666; line-height: 1.5; }
</style>
</head>
<body>
<div class="card">
  <h1>Link Expired</h1>
  <p>This link has expired or has already been used.<br>Run <strong>/setup</strong> in the bot to get a new link.</p>
</div>
</body>
</html>
"""


# ── Security headers ──

_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


# ── aiohttp handlers ──


async def handle_link_page(request: web.Request) -> web.Response:
    """GET /link?token=<otp> — serve the HTML form."""
    otp = request.query.get("token", "")
    if not otp or otp not in _otp_store:
        return web.Response(text=_EXPIRED_PAGE, content_type="text/html", headers=_SECURITY_HEADERS)

    # Check expiry without consuming
    _, expiry = _otp_store[otp]
    if _time.time() > expiry:
        _otp_store.pop(otp, None)
        return web.Response(text=_EXPIRED_PAGE, content_type="text/html", headers=_SECURITY_HEADERS)

    html = _LINK_PAGE % {"otp": html_mod.escape(otp)}
    return web.Response(text=html, content_type="text/html", headers=_SECURITY_HEADERS)


async def handle_link_submit(request: web.Request) -> web.Response:
    """POST /link/submit — validate OTP + Canvas token, save, notify user."""
    data = await request.post()
    otp = data.get("otp", "")
    token = data.get("token", "").strip()

    telegram_id = _consume_otp(otp)
    if telegram_id is None:
        return web.json_response(
            {"ok": False, "message": "This link has expired or was already used. Run /setup again in the bot."}
        )

    if not token or len(token) < 10:
        return web.json_response(
            {"ok": False, "message": "That doesn't look like a valid Canvas token."}
        )

    # Validate the token against Canvas API
    try:
        courses = await canvas.get_courses(token)
    except Exception:
        return web.json_response(
            {"ok": False, "message": "That token doesn't seem to work. Please check it and try again."}
        )

    await models.upsert_user(telegram_id, token, token_source="web")
    canvas.clear_course_cache(token)

    # Send Telegram confirmation
    bot_app = request.app.get("bot_app")
    if bot_app:
        try:
            await bot_app.bot.send_message(
                chat_id=telegram_id,
                text=(
                    f"Canvas token verified and saved! Found {len(courses)} active course(s).\n\n"
                    "Try /assignments or /due to see your assignments.\n\n"
                    "Remember:\n"
                    "- /setup — replace your token anytime\n"
                    "- /unlink — remove your token & all data"
                ),
            )
        except Exception:
            logger.warning("Could not send Telegram confirmation to user %s", telegram_id)

    return web.json_response(
        {"ok": True, "message": f"Canvas account linked! Found {len(courses)} active course(s). You can close this page."}
    )


def create_web_app(bot_app) -> web.Application:
    """Create the aiohttp Application, storing a reference to the Telegram bot."""
    app = web.Application(client_max_size=64 * 1024)  # 64 KB
    app["bot_app"] = bot_app
    app.router.add_get("/link", handle_link_page)
    app.router.add_post("/link/submit", handle_link_submit)
    return app
