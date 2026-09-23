"""One-time Telegram group membership check for first-time store visitors."""
import os
import sqlite3
from pathlib import Path

GROUP_ID = os.getenv("REQUIRED_GROUP_ID", "@SAU2030_k")
GROUP_URL = "https://t.me/SAU2030_k"
DB_PATH = Path(os.getenv("STORE_STATE_PATH", "/data/storefront.sqlite3"))


def approved(cid):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH, timeout=15) as db:
        db.execute("CREATE TABLE IF NOT EXISTS group_join_approved (cid INTEGER PRIMARY KEY)")
        return db.execute("SELECT 1 FROM group_join_approved WHERE cid=?", (cid,)).fetchone() is not None


def prompt(api, cid, message=None):
    text = message or "🔒 للمتابعة إلى المتجر، انضم إلى مجموعتنا أولًا ثم اضغط «تحقق من الاشتراك».\\nبعد التحقق بنجاح لن يُطلب منك الانضمام مرة أخرى."
    api.call("sendMessage", chat_id=cid, text=text, reply_markup={
        "inline_keyboard": [
            [{"text": "📢 الانضمام إلى المجموعة", "url": GROUP_URL}],
            [{"text": "✅ تحقق من الاشتراك", "callback_data": "required_group:verify"}]
        ]
    })


def verify(api, cid):
    result = api.call("getChatMember", chat_id=GROUP_ID, user_id=cid)
    if not isinstance(result, dict) or "status" not in result:
        prompt(api, cid, "⚠️ تعذر التحقق حاليًا. تأكد أن البوت مشرف في المجموعة ثم حاول مجددًا.")
        return False
    status = result["status"]
    if status in ("creator", "administrator", "member") or (status == "restricted" and result.get("is_member")):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(DB_PATH, timeout=15) as db:
            db.execute("CREATE TABLE IF NOT EXISTS group_join_approved (cid INTEGER PRIMARY KEY)")
            db.execute("INSERT OR IGNORE INTO group_join_approved (cid) VALUES (?)", (cid,))
        api.call("sendMessage", chat_id=cid, text="✅ تم التحقق بنجاح! أهلاً بك في المتجر.")
        return True
    prompt(api, cid, "❌ لم يتم العثور على عضويتك في المجموعة. انضم أولًا ثم اضغط تحقق من الاشتراك.")
    return False
