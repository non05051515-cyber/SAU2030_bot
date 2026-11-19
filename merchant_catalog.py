"""Merchant catalogue and owner-configured product announcements."""
import re
from decimal import Decimal

import product_announcements
import storefront as s

APPLE = "custom_chatgpt_plus_applepay"
RESET = "merchant_catalog_public_selection_20261119"
CATEGORY_NAMES = {
    "chatgpt": "ChatGPT", "gemini": "Gemini", "canva": "Canva",
    "claude": "Claude", "capcut": "CapCut", "spotify": "Spotify",
    "gamma": "Gamma AI", "netflix": "Netflix", "duolingo": "Duolingo",
    "youtube": "YouTube", "microsoft": "Microsoft 365", "figma": "Figma",
    "adobe": "Adobe", "turnitin": "Turnitin", "grok": "Grok",
    "elsa": "Elsa Speak", "veo": "Veo 4", "kling": "Kling",
    "scribd": "Scribd", "zoom": "Zoom", "cursor": "Cursor",
    "elevenlabs": "ElevenLabs", "daughter": "Daughter",
    "heygen": "HeyGen", "wink": "Wink",
}
PAGE_SIZE = 12


def setup():
    with s.db() as db:
        db.execute("CREATE TABLE IF NOT EXISTS merchant_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("""CREATE TABLE IF NOT EXISTS merchant_announcement_stock (
            pid TEXT PRIMARY KEY, stock INTEGER NOT NULL, available INTEGER NOT NULL
        )""")
        if db.execute("SELECT 1 FROM content_migrations WHERE key=?", (RESET,)).fetchone():
            return
        ids = set(s.VARIANTS) | set(s.G["PRODUCTS"]) | {
            row[0] for row in db.execute("SELECT pid FROM admin_products")
        }
        db.executemany(
            "INSERT OR REPLACE INTO product_visibility VALUES (?,0)",
            [(pid,) for pid in ids - {APPLE, *CATEGORY_NAMES}],
        )
        db.execute("INSERT OR REPLACE INTO product_visibility VALUES (?,1)", (APPLE,))
        db.execute(
            "UPDATE admin_products SET name=?,description=?,price_usd=?,price_sar=? WHERE pid=?",
            ("ChatGPT Plus (Apple Pay)", "اشتراك ChatGPT Plus لمدة شهر، مفعّل عبر Apple Pay. الضمان 29 يومًا.",
             "3.99", str((Decimal("3.99") * s.RATE).quantize(Decimal(".01"), rounding=s.ROUND_HALF_UP)), APPLE),
        )
        db.execute("INSERT OR REPLACE INTO product_prices VALUES (?,?,'USD')", (APPLE, "3.99"))
        db.executemany(
            "INSERT OR REPLACE INTO product_text (pid,field,lang,value) VALUES (?,?,?,?)",
            [(APPLE, "name", lang, "ChatGPT Plus (Apple Pay)") for lang in ("ar", "en")] + [
                (APPLE, "description", "ar", "اشتراك ChatGPT Plus لمدة شهر، مفعّل عبر Apple Pay. الضمان 29 يومًا."),
                (APPLE, "description", "en", "One month of ChatGPT Plus, activated via Apple Pay. Includes a 29-day warranty."),
            ],
        )
        db.execute("INSERT INTO content_migrations VALUES (?,?)", (RESET, s.now_saudi()))


def catalog():
    result = {section: [] for section in CATEGORY_NAMES}
    with s.db() as db:
        rows = db.execute("""
            SELECT pid, category_id, name FROM admin_products
            WHERE substr(pid, 1, 7) = 'custom_' ORDER BY rowid
        """).fetchall()
    for pid, category_id, name in rows:
        if not s.product_visible(pid):
            continue
        if category_id is None:
            lowered = name.casefold().replace(" ", "")
            category_id = "chatgpt" if "chatgpt" in lowered else "gemini" if "gemini" in lowered else "direct"
        result.setdefault(category_id, []).append(pid)
    return result


def stock(pid):
    return max(0, int(s.product_stock(pid)))


def unit_price(pid):
    value = s.amount(pid, "USD")
    return f"${value:.2f}" if value is not None else "—"


def category_name(cid, category):
    if category in CATEGORY_NAMES:
        return CATEGORY_NAMES[category]
    if category == "direct":
        return s.tr(cid, "منتجات أخرى", "Other products")
    return s.name(category, cid)


def home(api, cid):
    balance = s.wallet_balance(cid) / s.RATE
    text = s.tr(cid,
        "<b>أهلًا بك في VEXA STORE</b>\nاشتراكات رقمية بالجملة للتجّار وأصحاب البوتات.\n\n",
        "<b>Welcome to VEXA STORE</b>\nWholesale digital subscriptions for merchants and bot owners.\n\n",
    ) + s.tr(cid, f"💰 <b>رصيدك:</b> ${balance:.2f}", f"💰 <b>Balance:</b> ${balance:.2f}")
    rows = [
        [s.btn(s.tr(cid, "🛒 المتجر", "🛒 Shop"), "merchant:shop", style="success")],
        [s.btn(s.tr(cid, "👤 حسابي", "👤 My account"), "wallet"),
         s.btn(s.tr(cid, "💰 شحن الرصيد", "💰 Top up"), "wallet:topup", style="primary")],
        [s.btn(s.tr(cid, "🧾 طلباتي", "🧾 My orders"), "myorders"),
         s.btn(s.tr(cid, "💬 الدعم", "💬 Support"), "support")],
        [s.btn("العربية / English", "settings:lang")],
    ]
    if cid == s.G["ADMIN_ID"]:
        rows += [[s.btn("لوحة الإدارة", "admin", style="primary"),
                  s.btn("إعداد قناة الإعلانات", "merchant:channel", style="primary")]]
    return s.send(api, cid, text, s.kb(rows))


def setting(key, fallback=""):
    with s.db() as db:
        row = db.execute("SELECT value FROM merchant_settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else fallback


def shop(api, cid):
    groups = catalog()
    buttons = []
    for category, ids in groups.items():
        buttons.append(s.btn(category_name(cid, category), "merchant:section:" + category,
                             s.ui_icon(category), style="danger" if category in ("youtube", "daughter") else "success"))
    rows = [buttons[index:index + 3] for index in range(0, len(buttons), 3)]
    rows += [
        [s.btn(s.tr(cid, "💳 المحفظة", "💳 Wallet"), "wallet", style="primary"),
         s.btn(s.tr(cid, "🧾 طلباتي", "🧾 My orders"), "myorders")],
        [s.btn(s.tr(cid, "➕ شحن الرصيد", "➕ Top up"), "wallet:topup", style="success"),
         s.btn(s.tr(cid, "💬 الدعم", "💬 Support"), "support")],
        [s.btn("العربية / English", "settings:lang")],
    ]
    if cid == s.G["ADMIN_ID"]:
        rows.append([s.btn("لوحة الإدارة", "admin", style="primary"),
                     s.btn("تعديل روابط الترحيب", "merchant:links", style="primary")])
        rows.append([s.btn("إنشاء وصف مقترح", "merchant:describe", style="primary")])
    channel = setting("welcome_channel_url", "https://example.com/channel")
    group = setting("welcome_group_url", "https://example.com/group")
    title = s.tr(cid, "<b>VEXA STORE — اشتراكات رقمية للتجّار</b>",
                 "<b>VEXA STORE — Digital subscriptions for merchants</b>")
    note = s.tr(cid, "اختر قسمًا لتصفح المنتجات والأسعار والكميات.",
                "Choose a category to browse products, prices and stock.")
    links = ('<a href="' + s.esc(channel) + '">' + s.tr(cid, "القناة", "Channel") + '</a> · ' +
             '<a href="' + s.esc(group) + '">' + s.tr(cid, "المجموعة", "Group") + '</a>')
    return s.send(api, cid, title + "\n\n" + links + "\n\n" + note, s.kb(rows))


def owner_input(api, message):
    cid = message.get("chat", {}).get("id")
    if cid != s.G["ADMIN_ID"]:
        return False
    with s.db() as db:
        row = db.execute("SELECT action,value FROM admin_state WHERE cid=? AND action='merchant_links'", (cid,)).fetchone()
    if not row:
        return False
    raw = (message.get("text") or "").strip()
    if raw.startswith("/"):
        with s.db() as db:
            db.execute("DELETE FROM admin_state WHERE cid=? AND action='merchant_links'", (cid,))
        return False
    from urllib.parse import urlsplit
    parts = raw.split()
    valid = len(parts) == 2
    for url in parts:
        try:
            parsed = urlsplit(url)
            valid = valid and parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
        except ValueError:
            valid = False
    if not valid:
        s.send(api, cid, "أرسل رابط القناة ثم رابط المجموعة، كل رابط بسطر ويبدأ بـ https://")
        return True
    with s.db() as db:
        db.executemany("INSERT OR REPLACE INTO merchant_settings VALUES (?,?)",
                       [("welcome_channel_url", parts[0]), ("welcome_group_url", parts[1])])
        db.execute("DELETE FROM admin_state WHERE cid=? AND action='merchant_links'", (cid,))
    shop(api, cid)
    return True


def description_draft(pid):
    name = s.name(pid, s.G["ADMIN_ID"])
    cp = s.custom_product(pid)
    category = cp[5] if cp else ""
    uses = {
        "chatgpt": ("مناسب للمحادثات والكتابة وتلخيص المحتوى.", "For conversations, writing and summarizing content."),
        "gemini": ("مناسب للمساعدة في الكتابة وتنظيم الأفكار.", "For writing assistance and organizing ideas."),
        "canva": ("مناسب لتصميم العروض والمحتوى البصري.", "For presentations and visual content."),
        "claude": ("مناسب للكتابة ومراجعة النصوص.", "For writing and reviewing text."),
        "capcut": ("مناسب لتحرير الفيديو وصناعة المحتوى.", "For video editing and content creation."),
    }
    ar, en = uses.get(category, ("منتج رقمي للاستخدام حسب الباقة المختارة.", "A digital product for use according to the selected plan."))
    return name + "\n" + ar, name + "\n" + en


def description_preview(api, cid, pid):
    if not s.custom_product(pid):
        return s.send(api, cid, "اختر منتجًا مضافًا من لوحة الإدارة.")
    ar, en = description_draft(pid)
    return s.send(api, cid, "<b>وصف مقترح للمراجعة</b>\n\n" + s.esc(ar) + "\n\n" + s.esc(en),
        s.kb([[s.btn("حفظ الوصف بالعربي والإنجليزي", "merchant:desc_save:" + pid, style="success")],
              [s.btn("رجوع", "merchant:describe")]]))


def section(api, cid, category, page=0):
    groups = catalog()
    if category not in groups:
        return shop(api, cid)
    candidates = groups[category]
    page = min(max(0, page), max(0, (len(candidates) - 1) // PAGE_SIZE))
    rows = []
    for pid in candidates[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        quantity = stock(pid)
        title = ("🍋 " if pid == APPLE else "") + s.name(pid, cid)
        quantity_label = s.tr(cid, f"الكمية: {quantity}", f"Stock: {quantity}")
        rows.append([s.btn(f"{title} · {unit_price(pid)} · {quantity_label}", "options:" + pid,
                           s.product_button_icon(pid, cid) or s.ui_icon(category),
                           style="success" if quantity and s.can_order(pid) else "danger")])
    if page or (page + 1) * PAGE_SIZE < len(candidates):
        nav = []
        if page:
            nav.append(s.btn(s.tr(cid, "السابق", "Previous"), f"merchant:section:{category}:{page - 1}"))
        if (page + 1) * PAGE_SIZE < len(candidates):
            nav.append(s.btn(s.tr(cid, "التالي", "Next"), f"merchant:section:{category}:{page + 1}"))
        rows.append(nav)
    rows.append([s.btn(s.tr(cid, "↩️ التصنيفات", "↩️ Categories"), "merchant:shop"),
                 s.btn(s.tr(cid, "🔄 تحديث", "🔄 Refresh"), f"merchant:section:{category}:{page}", style="primary")])
    message = s.tr(cid, "اختر المنتج لعرض الوصف والتفاصيل.", "Choose a product to see details.") if candidates else s.tr(cid, "لا توجد منتجات في هذا القسم حاليًا.", "There are no products in this category yet.")
    return s.send(api, cid, "<b>" + s.esc(category_name(cid, category)) + "</b>\n\n" + message, s.kb(rows))


def channel_target():
    with s.db() as db:
        row = db.execute("SELECT value FROM merchant_settings WHERE key='announcement_channel'").fetchone()
    return int(row[0]) if row else None


def channel_settings(api, cid):
    selected = channel_target()
    text = ("📣 <b>قناة إعلانات المنتجات</b>\n\n" +
            ("مفعّلة للقناة: <code>" + str(selected) + "</code>" if selected else "ما فيه قناة مضافة."))
    text += "\nإذا فعّلت قناة، يعلن البوت فيها عن المنتجات الجديدة والتعبئة. لازم يكون البوت مشرفًا بصلاحية النشر."
    return s.send(api, cid, text, s.kb([
        [s.btn("➕ إضافة أو تغيير القناة", "merchant:channel_set", style="primary")],
        [s.btn("إيقاف النشر في القناة", "merchant:channel_off", style="danger")],
        [s.btn("↩️ لوحة الإدارة", "admin")],
    ]))


def channel_input(api, message):
    cid = message.get("chat", {}).get("id")
    if cid != s.G["ADMIN_ID"]:
        return False
    with s.db() as db:
        pending = db.execute("SELECT 1 FROM admin_state WHERE cid=? AND action='merchant_channel'", (cid,)).fetchone()
    if not pending:
        return False
    raw = (message.get("text") or "").strip()
    if raw.startswith("/"):
        with s.db() as db:
            db.execute("DELETE FROM admin_state WHERE cid=? AND action='merchant_channel'", (cid,))
        return False
    forward = message.get("forward_origin") or {}
    chat = forward.get("chat") if forward.get("type") == "channel" else None
    target = chat.get("id") if isinstance(chat, dict) else None
    if target is None:
        raw = raw.removeprefix("https://t.me/").removeprefix("http://t.me/").strip("/")
        if re.fullmatch(r"-100[0-9]{5,17}", raw):
            target = int(raw)
        elif re.fullmatch(r"@?[A-Za-z][A-Za-z0-9_]{4,31}", raw):
            target = "@" + raw.removeprefix("@")
    info = api.call("getChat", chat_id=target) if target is not None else None
    me = api.call("getMe") if isinstance(info, dict) and info.get("type") == "channel" else None
    member = api.call("getChatMember", chat_id=info["id"], user_id=me["id"]) if isinstance(me, dict) and me.get("id") else None
    if not isinstance(member, dict) or not (member.get("status") == "creator" or member.get("status") == "administrator" and member.get("can_post_messages")):
        s.send(api, cid, "تعذر التحقق من صلاحية النشر. أضف البوت مشرفًا في القناة ثم أرسل معرف القناة أو حوّل منشورًا منها.")
        return True
    with s.db() as db:
        db.execute("INSERT OR REPLACE INTO merchant_settings VALUES ('announcement_channel',?)", (str(info["id"]),))
        db.execute("DELETE FROM admin_state WHERE cid=? AND action='merchant_channel'", (cid,))
    channel_settings(api, cid)
    return True


def announcements(api):
    current = []
    for group in catalog().values():
        for pid in group:
            current.append((pid, stock(pid), int(s.can_order(pid))))
    with s.db() as db:
        known = {pid: (qty, available) for pid, qty, available in db.execute("SELECT * FROM merchant_announcement_stock")}
        ready = bool(db.execute("SELECT 1 FROM merchant_settings WHERE key='announcement_initialized'").fetchone())
        db.execute("INSERT OR IGNORE INTO merchant_settings VALUES ('announcement_initialized','1')")
        db.executemany("INSERT OR REPLACE INTO merchant_announcement_stock VALUES (?,?,?)", current)
    target = channel_target()
    if not ready or target is None:
        return
    for pid, qty, available in current:
        previous_qty, previous_available = known.get(pid, (0, 0))
        if qty <= 0 or not available or (previous_available and qty <= previous_qty):
            continue
        if channel_target() != target:
            break
        data = {"pid": pid, "added": max(0, qty - previous_qty), "stock": qty, "language": s.prefs(s.G["ADMIN_ID"])[0]}
        if not product_announcements.send_card(api, target, data, language=data["language"]):
            s.send(api, s.G["ADMIN_ID"], "تعذر تأكيد نشر إعلان المنتج: " + s.esc(s.name(pid, s.G["ADMIN_ID"])))


def install(namespace):
    original_action = namespace["action"]
    original_input = namespace["handle_admin_delivery"]

    def action(api, cid, value):
        admin = cid == s.G["ADMIN_ID"]
        if value.startswith(("merchant:links", "merchant:describe", "merchant:desc_")):
            if not admin:
                return
            if value == "merchant:links":
                with s.db() as db:
                    db.execute("INSERT OR REPLACE INTO admin_state VALUES (?,'merchant_links','')", (cid,))
                return s.send(api, cid, "أرسل رابط القناة ثم رابط المجموعة، كل واحد بسطر. الروابط الحالية أمثلة مؤقتة.")
            if value == "merchant:describe":
                with s.db() as db:
                    ids = [row[0] for row in db.execute("SELECT pid FROM admin_products ORDER BY rowid DESC LIMIT 35")]
                rows = [[s.btn(s.name(pid, cid), "merchant:desc_preview:" + pid)] for pid in ids]
                return s.send(api, cid, "اختر منتجًا لتجهيز وصف مقترح. راجعه قبل الحفظ.", s.kb(rows + [[s.btn("رجوع", "admin")]]))
            pid = value.split(":", 2)[2]
            if value.startswith("merchant:desc_preview:"):
                return description_preview(api, cid, pid)
            if s.custom_product(pid):
                ar, en = description_draft(pid)
                with s.db() as db:
                    db.execute("UPDATE admin_products SET description=? WHERE pid=?", (ar, pid))
                    db.executemany("INSERT OR REPLACE INTO product_text VALUES (?,'description',?,?)",
                                   [(pid, "ar", ar), (pid, "en", en)])
                return s.send(api, cid, "تم حفظ الوصف المقترح.")
            return
        if value in ("products", "merchant:shop"):
            return shop(api, cid)
        if value in ("home", "enter_store"):
            return shop(api, cid)
        if value.startswith("merchant:section:"):
            parts = value.split(":")
            page = int(parts[3]) if len(parts) > 3 and parts[3].isascii() and parts[3].isdigit() and len(parts[3]) <= 4 else 0
            return section(api, cid, parts[2], page)
        if value.startswith("product:") and (value[8:] in catalog() or not admin):
            return section(api, cid, value[8:])
        if value.startswith(("options:", "item:", "buy:")) and not admin:
            pid = value.split(":", 1)[1]
            if not any(pid in group for group in catalog().values()):
                return shop(api, cid)
        if value.startswith(("merchant:channel", "channel:", "ad:send:group", "admin:autoad")):
            if not admin:
                return
            if value == "merchant:channel_set":
                with s.db() as db:
                    db.execute("INSERT OR REPLACE INTO admin_state VALUES (?,'merchant_channel','')", (cid,))
                return s.send(api, cid, "أرسل معرف القناة، أو رقمها الذي يبدأ بـ ‎-100، أو حوّل منشورًا منها.")
            with s.db() as db:
                db.execute("DELETE FROM admin_state WHERE cid=? AND action='merchant_channel'", (cid,))
                if value == "merchant:channel_off":
                    db.execute("DELETE FROM merchant_settings WHERE key='announcement_channel'")
            if value.startswith(("channel:publish_new:", "channel:defer_new:")):
                if value.startswith("channel:defer_new:"):
                    with s.db() as db:
                        db.executemany("INSERT OR REPLACE INTO merchant_announcement_stock VALUES (?,?,?)",
                            [(pid, stock(pid), int(s.can_order(pid))) for pid in value.split(":", 2)[2].split(",") if s.custom_product(pid)])
                    return s.send(api, cid, "تم حفظ المنتجات.")
                if channel_target() is not None:
                    return s.send(api, cid, "إعلانات المنتجات تُرسل تلقائيًا للقناة المضافة عند توفر المخزون.")
                visible = {pid for group in catalog().values() for pid in group}
                for pid in value.split(":", 2)[2].split(",")[:5]:
                    if pid in visible:
                        qty = stock(pid)
                        product_announcements.send_card(api, cid, {"pid": pid, "added": qty, "stock": qty}, language=s.prefs(cid)[0])
            return channel_settings(api, cid)
        return original_action(api, cid, value)

    def message_input(api, message):
        return owner_input(api, message) or channel_input(api, message) or original_input(api, message)

    s.home, s.products = shop, shop
    namespace.update(action=action, handle_action=action, show_start=shop, show_home=shop, show_products=shop,
                     handle_admin_delivery=message_input, tick_channel_catalog=announcements,
                     tick_auto_ads=None)
