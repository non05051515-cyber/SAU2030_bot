"""ChatGPT customer chat extension. Keeps store behavior unchanged outside chat mode."""
import json
import os
import time
import threading
import sqlite3
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import storefront as s

DB_PATH = Path(os.getenv("CHATGPT_STATE_PATH", "/data/chatgpt_chat.sqlite3"))
MAX_DAILY = 15
MAX_HISTORY = 5
_INSTALLED = False


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (cid INTEGER PRIMARY KEY, active INTEGER NOT NULL DEFAULT 0, history TEXT NOT NULL DEFAULT '[]')")
    conn.execute("CREATE TABLE IF NOT EXISTS usage (cid INTEGER NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(cid,day))")
    return conn


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def is_active(cid):
    with db() as conn:
        row = conn.execute("SELECT active FROM sessions WHERE cid=?", (cid,)).fetchone()
    return bool(row and row[0])


def start_chat(api, cid):
    with db() as conn:
        conn.execute("INSERT INTO sessions(cid,active,history) VALUES (?,1,\'[]\') ON CONFLICT(cid) DO UPDATE SET active=1, history=\'[]\'", (cid,))
    s.send(api, cid, s.tr(cid, "بدأت المحادثة مع ChatGPT. أرسل رسالتك الآن.", "ChatGPT conversation started. Send your message now."), s.menu(cid))


def end_chat(api, cid, go_home=False):
    with db() as conn:
        conn.execute("INSERT INTO sessions(cid,active,history) VALUES (?,0,'[]') ON CONFLICT(cid) DO UPDATE SET active=0, history='[]'", (cid,))
    if go_home:
        return s.home(api, cid)
    s.send(api, cid, s.tr(cid, "تم إنهاء المحادثة.", "Conversation ended."), s.menu(cid))


def ask_model(cid, text):
    key = os.getenv("MIRAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("MIRAI_API_KEY missing")
    base = os.getenv("MIRAI_API_BASE", "https://api.miraiapi.com/v1").rstrip("/")
    model = os.getenv("MIRAI_MODEL", "claude-opus-4.8")
    with db() as conn:
        row = conn.execute("SELECT history FROM sessions WHERE cid=?", (cid,)).fetchone()
        try:
            history = json.loads(row[0]) if row else []
        except Exception:
            history = []
    input_messages = [{"role": "system", "content": "You are a general-purpose helpful AI assistant. Answer the user's questions naturally across general topics. Reply in the same language as the user unless they ask for another language. Be concise, accurate, and helpful."}]
    input_messages.extend(history[-MAX_HISTORY:])
    input_messages.append({"role": "user", "content": text})
    payload = json.dumps({"model": model, "messages": input_messages, "max_tokens": 700}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + "/chat/completions", payload, {
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; SAU2030Bot/1.0)",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")[:1000]
        except Exception:
            body = ""
        print("Mirai HTTP error:", exc.code, body)
        raise
    answer = ""
    try:
        answer = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        pass
    if not answer:
        raise RuntimeError("Empty model response")
    new_history = (history + [{"role": "user", "content": text}, {"role": "assistant", "content": answer}])[-MAX_HISTORY:]
    with db() as conn:
        conn.execute("UPDATE sessions SET history=? WHERE cid=?", (json.dumps(new_history, ensure_ascii=False), cid))
    return answer


def handle_chat_message(api, message):
    cid = message.get("chat", {}).get("id")
    if not cid or not is_active(cid):
        return False
    text = (message.get("text") or "").strip()
    if text in ("إنهاء المحادثة", "End conversation"):
        end_chat(api, cid)
        return True
    if text in ("الرجوع للصفحة الرئيسية", "Back to home"):
        end_chat(api, cid, True)
        return True
    if not text:
        s.send(api, cid, s.tr(cid, "أرسل رسالة نصية للمحادثة.", "Send a text message to chat."))
        return True
    # Normal store/menu commands must leave ChatGPT mode instead of being sent to Mirai.
    menu_actions = s.G.get("MENU", {})
    if text.startswith("/start") or (text in menu_actions and menu_actions.get(text) not in ("chatgpt", "chatgpt:start", "chatgpt:end", "chatgpt:home")):
        with db() as conn:
            conn.execute("INSERT INTO sessions(cid,active,history) VALUES (?,0,'[]') ON CONFLICT(cid) DO UPDATE SET active=0, history='[]'", (cid,))
        return False
    with db() as conn:
        row = conn.execute("SELECT count FROM usage WHERE cid=? AND day=?", (cid, today())).fetchone()
        used = row[0] if row else 0
    if used >= MAX_DAILY:
        s.send(api, cid, s.tr(cid, "وصلت إلى الحد اليومي للمحادثة (15 رسالة).", "You reached today's chat limit (15 messages)."),
               s.kb([[s.btn(s.tr(cid, "إنهاء المحادثة", "End conversation"), "chatgpt:end", s.ui_icon("ui_chatgpt_end"))],
                     [s.btn(s.tr(cid, "الرجوع للصفحة الرئيسية", "Back to home"), "chatgpt:home", s.ui_icon("ui_chatgpt_home"))]]))
        return True
    try:
        typing_stop = threading.Event()
        def keep_typing():
            while not typing_stop.is_set():
                try:
                    api.call("sendChatAction", chat_id=cid, action="typing")
                except Exception:
                    pass
                typing_stop.wait(4)
        typing_thread = threading.Thread(target=keep_typing, daemon=True)
        typing_thread.start()
        started_at = time.monotonic()
        try:
            answer = ask_model(cid, text)
        finally:
            typing_stop.set()
        print("Mirai response time:", round(time.monotonic() - started_at, 2), "seconds")
    except Exception as exc:
        print("ChatGPT API error:", type(exc).__name__)
        if isinstance(exc, RuntimeError) and "MIRAI_API_KEY" in str(exc):
            msg = s.tr(cid, "خدمة ChatGPT لم تُربط بمفتاح API على الخادم بعد.", "ChatGPT is not connected to an API key on the server yet.")
        else:
            msg = s.tr(cid, "تعذر الحصول على رد الآن. حاول مرة أخرى.", "Could not get a response right now. Please try again.")
        s.send(api, cid, msg)
        return True
    with db() as conn:
        conn.execute("INSERT INTO usage(cid,day,count) VALUES (?,?,1) ON CONFLICT(cid,day) DO UPDATE SET count=count+1", (cid, today()))
        used = conn.execute("SELECT count FROM usage WHERE cid=? AND day=?", (cid, today())).fetchone()[0]
    s.send(api, cid, answer, s.kb([[s.btn(s.tr(cid, "إنهاء المحادثة", "End conversation"), "chatgpt:end"),
                                   s.btn(s.tr(cid, "الرجوع للصفحة الرئيسية", "Back to home"), "chatgpt:home", s.ui_icon("ui_chatgpt_home"))]]))
    return True


def install(namespace):
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    old_home = s.home
    old_action = namespace["action"]
    old_receipt = namespace["handle_receipt"]
    old_menu = s.menu

    def menu(cid=0):
        markup = old_menu(cid)
        rows = list(markup.get("keyboard", []))
        admin_index = len(rows)
        if cid == s.G.get("ADMIN_ID") and rows:
            admin_index -= 1
        rows.insert(admin_index, [{"text": s.tr(cid, "التحدث مع ChatGPT", "Chat with ChatGPT")}])
        markup["keyboard"] = rows
        return markup

    def home(api, cid):
        balance_sar = s.wallet_balance(cid)
        balance_usd = (balance_sar / s.RATE).quantize(s.Decimal("0.01"), rounding=s.ROUND_HALF_UP)
        with s.db() as conn:
            purchases = conn.execute('SELECT COUNT(*) FROM orders WHERE cid=? AND status="paid"', (cid,)).fetchone()[0]
        text = s.tr(cid, f'👋 <b>أهلاً بك في VEXA STORE!</b>\n\n🆔 رقم العضوية: <code>{cid}</code>\n👤 حسابك: <a href="tg://user?id={cid}">فتح الحساب</a>\n💳 الرصيد: <b>${balance_usd:.2f}</b>\n🛍 المشتريات: <b>{purchases}</b>\n\nاختر من القائمة أدناه:',
                    f'👋 <b>Welcome to VEXA STORE!</b>\n\n🆔 Member ID: <code>{cid}</code>\n👤 Account: <a href="tg://user?id={cid}">Open profile</a>\n💳 Balance: <b>${balance_usd:.2f}</b>\n🛍 Purchases: <b>{purchases}</b>\n\nChoose from the menu below:')
        rows = [[s.btn(s.tr(cid,'🛒 المنتجات','🛒 Products'),'products',s.ui_icon('ui_products'),style='primary'), s.btn(s.tr(cid,'💰 شحن الرصيد','💰 Top up'),'wallet:topup',s.ui_icon('ui_topup'),style='success')],
                [s.btn(s.tr(cid,'💎 الإحالات','💎 Referrals'),'referrals',s.ui_icon('ui_referrals')), s.btn(s.tr(cid,'👤 حسابي','👤 My account'),'wallet',s.ui_icon('ui_account'))],
                [s.btn(s.tr(cid,'💬 تواصل مع الدعم','💬 Contact support'),'support',s.ui_icon('ui_support'),style='danger'), s.btn(s.tr(cid,'⚠️ إبلاغ عن مشكلة','⚠️ Report issue'),'support',s.ui_icon('ui_report'))],
                [s.btn(s.tr(cid,'💱 العملة','💱 Currency'),'settings:currency',s.ui_icon('ui_currency')), s.btn('🌐 Language / اللغة','settings:lang',s.ui_icon('ui_language'))],
                [s.btn(s.tr(cid, 'تحت الصيانة (التحدث مع Ai)', 'Under maintenance (Chat with AI)'), 'chatgpt:maintenance', s.ui_icon('ui_chatgpt'), style='success')]]
        if cid == s.G.get("ADMIN_ID"):
            rows.append([s.btn('🧾 لوحة الطلبات', 'admin', s.ui_icon('ui_admin'), style='primary')])
        s.send(api, cid, text, s.kb(rows))

    def action(api, cid, value):
        if value in ("chatgpt", "chatgpt:start"):
            return start_chat(api, cid)
        if value == "chatgpt:end":
            return end_chat(api, cid)
        if value == "chatgpt:home":
            return end_chat(api, cid, True)
        return old_action(api, cid, value)

    def receipt(api, message):
        if handle_chat_message(api, message):
            return True
        return old_receipt(api, message)

    s.menu = menu
    s.home = home
    namespace["home_keyboard"] = menu
    namespace["show_home"] = home
    namespace["action"] = action
    namespace["handle_action"] = action
    namespace["handle_receipt"] = receipt
    menu_actions = namespace.setdefault("MENU_ACTIONS", namespace.get("MENU", {}))
    menu_actions.update({"التحدث مع ChatGPT": "chatgpt", "Chat with ChatGPT": "chatgpt",
                         "إنهاء المحادثة": "chatgpt:end", "End conversation": "chatgpt:end",
                         "الرجوع للصفحة الرئيسية": "chatgpt:home", "Back to home": "chatgpt:home"})
    namespace["MENU"] = menu_actions
