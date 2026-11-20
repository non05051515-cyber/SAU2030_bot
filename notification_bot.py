"""Soq ID storefront notifications with a durable, opt-out-aware delivery queue."""
import hmac
import html
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA = Path(os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "/data"))
DATABASE = DATA / "soqid-notifications.sqlite3"
STORE_URL = os.getenv("STOREFRONT_URL", "").rstrip("/")
ADMIN_ID = int(os.getenv("NOTIFICATIONS_ADMIN_ID", "8386371522"))
BOT_USERNAME = ""
MAX_BODY = 16000


class TelegramError(Exception):
    def __init__(self, code, retry_after=0):
        super().__init__("Telegram request failed")
        self.code = code
        self.retry_after = retry_after


def telegram(method, **params):
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + os.environ["BOT_TOKEN"] + "/" + method,
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        try:
            body = json.load(error)
        except (ValueError, OSError):
            raise TelegramError(error.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TelegramError(0) from None
    if not body.get("ok"):
        raise TelegramError(
            body.get("error_code", 0),
            body.get("parameters", {}).get("retry_after", 0),
        )
    return body.get("result")


def db():
    connection = sqlite3.connect(DATABASE, timeout=15)
    connection.execute("PRAGMA busy_timeout=15000")
    connection.row_factory = sqlite3.Row
    return connection


def load_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def setup():
    DATA.mkdir(parents=True, exist_ok=True)
    with db() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS subscribers (
                cid INTEGER PRIMARY KEY, language TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                received_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                event_id TEXT NOT NULL, cid INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                retry_at INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(event_id,cid)
            );
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
        """)
        migrated = connection.execute(
            "SELECT 1 FROM bot_state WHERE key='legacy_subscribers_imported'"
        ).fetchone()
        if migrated:
            return
        users = load_json(DATA / "users.json", [])
        languages = load_json(DATA / "languages.json", {})
        preferences = {}
        legacy_path = Path(os.getenv("STORE_STATE_PATH", str(DATA / "storefront.sqlite3")))
        if legacy_path.exists():
            with sqlite3.connect(legacy_path.as_uri() + "?mode=ro", uri=True) as legacy:
                tables = {row[0] for row in legacy.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "advertising_preferences" in tables:
                    preferences = dict(legacy.execute("SELECT cid,enabled FROM advertising_preferences"))
                if "preferences" in tables:
                    languages.update({str(cid): language for cid, language in legacy.execute("SELECT cid,lang FROM preferences")})
        for value in users:
            try:
                cid = int(value)
            except (ValueError, TypeError):
                continue
            if cid <= 0:
                continue
            language = "en" if languages.get(str(cid)) == "en" else "ar"
            connection.execute(
                "INSERT OR IGNORE INTO subscribers(cid,language,enabled) VALUES (?,?,?)",
                (cid, language, int(bool(preferences.get(cid, 1)))),
            )
        connection.execute(
            "INSERT INTO bot_state(key,value) VALUES ('legacy_subscribers_imported','1')"
        )


def preference(cid):
    with db() as connection:
        row = connection.execute(
            "SELECT language,enabled FROM subscribers WHERE cid=?", (cid,)
        ).fetchone()
    return (row["language"], bool(row["enabled"])) if row else ("ar", False)


def subscribe(cid, enabled, language=None):
    current_language, _ = preference(cid)
    language = language if language in ("ar", "en") else current_language
    with db() as connection:
        connection.execute(
            "INSERT INTO subscribers(cid,language,enabled) VALUES (?,?,?) "
            "ON CONFLICT(cid) DO UPDATE SET language=excluded.language,enabled=excluded.enabled",
            (cid, language, int(enabled)),
        )


def store_link(product_id=None):
    return STORE_URL + ("/?product=" + urllib.parse.quote(product_id, safe="") if product_id else "/")


def keyboard(cid):
    language, enabled = preference(cid)
    english = language == "en"
    rows = [
        [{"text": "Open store" if english else "افتح متجر سوق ID", "url": store_link()}],
        [{"text": ("Stop notifications" if english else "إيقاف الإشعارات") if enabled else
                  ("Enable notifications" if english else "تفعيل الإشعارات"),
          "callback_data": "notifications:off" if enabled else "notifications:on"}],
        [{"text": "العربية" if english else "English", "callback_data": "notifications:language"}],
    ]
    if cid == ADMIN_ID:
        rows.append([{"text": "Store administration" if english else "لوحة تحكم المتجر", "url": STORE_URL + "/admin"}])
    return {"inline_keyboard": rows}


def welcome(cid):
    language, enabled = preference(cid)
    english = language == "en"
    text = (
        "Soq ID\nStock notifications and product updates. Open the store to browse and order.\n\n"
        + ("Notifications enabled." if enabled else "Notifications paused.")
        if english else
        "سوق ID\nإشعارات المخزون وتحديثات المنتجات. افتح المتجر للتصفح والطلب.\n\n"
        + ("الإشعارات مفعّلة." if enabled else "الإشعارات متوقفة.")
    )
    telegram("sendMessage", chat_id=cid, text=text, reply_markup=keyboard(cid))


def process_update(update):
    callback = update.get("callback_query")
    if callback:
        cid = callback.get("from", {}).get("id")
        if not isinstance(cid, int) or cid <= 0:
            return
        value = callback.get("data", "")
        language, enabled = preference(cid)
        if value in ("notifications:off", "ads:stop"):
            subscribe(cid, False)
        elif value == "notifications:on":
            subscribe(cid, True)
        elif value == "notifications:language":
            subscribe(cid, enabled, "ar" if language == "en" else "en")
        telegram("answerCallbackQuery", callback_query_id=callback["id"])
        welcome(cid)
        return
    message = update.get("message", {})
    if message.get("chat", {}).get("type") != "private":
        return
    cid = message["chat"]["id"]
    text = str(message.get("text", ""))
    command = text.split(maxsplit=1)[0].split("@")[0] if text else ""
    if command == "/start":
        language = message.get("from", {}).get("language_code", "ar")
        subscribe(cid, True, "en" if language.startswith("en") else "ar")
    elif command == "/stop":
        subscribe(cid, False)
    elif command == "/language":
        language, enabled = preference(cid)
        subscribe(cid, enabled, "ar" if language == "en" else "en")
    welcome(cid)


def validate_event(data):
    if not isinstance(data, dict):
        raise ValueError("event")
    for key in ("id", "product_id"):
        if not isinstance(data.get(key), str) or not 1 <= len(data[key]) <= 80:
            raise ValueError(key)
    name = data.get("name")
    if not isinstance(name, list) or len(name) != 2 or not all(
        isinstance(value, str) and 0 < len(value) <= 160 for value in name
    ):
        raise ValueError("name")
    for key in ("added", "stock", "price_cents"):
        if type(data.get(key)) is not int or not 0 <= data[key] <= 10_000_000:
            raise ValueError(key)
    if data["added"] <= 0 or data["stock"] < data["added"]:
        raise ValueError("stock")
    return {key: data[key] for key in ("id", "product_id", "name", "added", "stock", "price_cents")}


def queue_event(data):
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True)
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT payload FROM events WHERE id=?", (data["id"],)).fetchone()
        if row:
            if row["payload"] != encoded:
                raise ValueError("event_id_conflict")
            return
        connection.execute(
            "INSERT INTO events(id,payload,received_at) VALUES (?,?,?)",
            (data["id"], encoded, int(time.time())),
        )
        connection.execute(
            "INSERT INTO deliveries(event_id,cid) SELECT ?,cid FROM subscribers WHERE enabled=1",
            (data["id"],),
        )


def notification(data, language):
    english = language == "en"
    title = html.escape(data["name"][1 if english else 0])
    amount = "$" + format(data["price_cents"] / 100, ".2f")
    text = (
        f"📦 <b>{title}</b>\n➕ Added: {data['added']}\n"
        f"Available now: {data['stock']}\n💵 Price: {amount}"
        if english else
        f"📦 <b>{title}</b>\n➕ تمت الإضافة: {data['added']}\n"
        f"الكمية المتوفرة حاليًا: {data['stock']}\n💵 السعر: {amount}"
    )
    return {
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [
            [{"text": "Open product" if english else "افتح المنتج", "url": store_link(data["product_id"])}],
            [{"text": "Stop notifications" if english else "إيقاف الإشعارات", "callback_data": "notifications:off"}],
        ]},
    }


def deliver_one():
    with db() as connection:
        row = connection.execute(
            "SELECT d.event_id,d.cid,e.payload,s.language,s.enabled "
            "FROM deliveries d JOIN events e ON e.id=d.event_id "
            "JOIN subscribers s ON s.cid=d.cid "
            "WHERE d.status='pending' AND d.retry_at<=? ORDER BY e.received_at,d.cid LIMIT 1",
            (int(time.time()),),
        ).fetchone()
    if not row:
        return False
    status = "skipped"
    retry_at = 0
    if row["enabled"]:
        try:
            telegram("sendMessage", chat_id=row["cid"], **notification(json.loads(row["payload"]), row["language"]))
            status = "sent"
        except TelegramError as error:
            if error.code in (400, 403):
                status = "unavailable"
                subscribe(row["cid"], False)
            else:
                status = "pending"
                retry_at = int(time.time()) + max(15, min(error.retry_after, 3600))
    with db() as connection:
        connection.execute(
            "UPDATE deliveries SET status=?,retry_at=? WHERE event_id=? AND cid=?",
            (status, retry_at, row["event_id"], row["cid"]),
        )
    return True


def delivery_loop():
    while True:
        try:
            worked = deliver_one()
            time.sleep(0.06 if worked else 1)
        except Exception as error:
            print("Notification worker error:", type(error).__name__, flush=True)
            time.sleep(5)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authenticated(self):
        expected = "Bearer " + os.getenv("STOCK_NOTIFICATIONS_SECRET", "")
        return len(expected) > 20 and hmac.compare_digest(
            self.headers.get("Authorization", "").encode(), expected.encode()
        )

    def do_GET(self):
        if self.path == "/health":
            return self.respond(200, {"status": "ok", "mode": "notifications", "bot": BOT_USERNAME})
        if self.path == "/internal/notifications" and self.authenticated():
            with db() as connection:
                pending = connection.execute("SELECT count(*) FROM deliveries WHERE status='pending'").fetchone()[0]
                subscribed = connection.execute("SELECT count(*) FROM subscribers WHERE enabled=1").fetchone()[0]
            return self.respond(200, {"pending": pending, "subscribed": subscribed, "mode": "notifications"})
        self.respond(404, {"error": "not_found"})

    def do_POST(self):
        if self.path != "/stock-notifications":
            return self.respond(404, {"error": "not_found"})
        if not self.authenticated():
            return self.respond(401, {"error": "unauthorized"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                return self.respond(413, {"error": "invalid_length"})
            data = validate_event(json.loads(self.rfile.read(length)))
            if self.headers.get("X-Notification-Dry-Run") == "1":
                return self.respond(200, {"validated": True})
            queue_event(data)
        except (ValueError, UnicodeError):
            return self.respond(400, {"error": "invalid_event"})
        except sqlite3.Error:
            return self.respond(503, {"error": "queue_unavailable"})
        self.respond(202, {"queued": True, "id": data["id"]})


def main():
    global BOT_USERNAME
    if not os.getenv("BOT_TOKEN") or len(os.getenv("STOCK_NOTIFICATIONS_SECRET", "")) < 24:
        raise RuntimeError("Notification service credentials are missing")
    if not STORE_URL.startswith("https://"):
        raise RuntimeError("STOREFRONT_URL must use HTTPS")
    setup()
    identity = telegram("getMe")
    BOT_USERNAME = identity["username"]
    if BOT_USERNAME.lower() != "sau2030_bot":
        raise RuntimeError("Notification mode is restricted to SAU2030_bot")
    telegram("deleteWebhook", drop_pending_updates=False)
    telegram("setMyName", name="سوق ID | Soq ID")
    telegram("setMyDescription", description="إشعارات مخزون سوق ID وتحديثات المنتجات. افتح المتجر للتصفح والطلب.")
    telegram("setMyShortDescription", short_description="سوق ID — إشعارات المخزون ورابط المتجر")
    telegram("setMyCommands", commands=[
        {"command": "start", "description": "المتجر والإشعارات | Store and notifications"},
        {"command": "stop", "description": "إيقاف الإشعارات | Stop notifications"},
        {"command": "language", "description": "اللغة | Language"},
    ])
    telegram("setChatMenuButton", menu_button={"type": "commands"})
    server = ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "8080"))), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="notification-http").start()
    threading.Thread(target=delivery_loop, daemon=True, name="notification-delivery").start()
    with db() as connection:
        row = connection.execute("SELECT value FROM bot_state WHERE key='offset'").fetchone()
        offset = int(row["value"]) if row else 0
    print("Soq ID notification mode ready:", BOT_USERNAME, flush=True)
    while True:
        try:
            updates = telegram("getUpdates", offset=offset, timeout=25, allowed_updates=["message", "callback_query"])
            for update in updates:
                try:
                    process_update(update)
                except TelegramError as error:
                    if error.code not in (400, 403):
                        raise
                offset = update["update_id"] + 1
                with db() as connection:
                    connection.execute(
                        "INSERT INTO bot_state(key,value) VALUES ('offset',?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(offset),)
                    )
        except Exception as error:
            print("Notification polling retry:", type(error).__name__, flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
