"""SAU2030 Telegram Store Bot - Python 3.10+"""

import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE = Path(__file__).resolve().parent

CONFIG = json.loads((BASE / "catalog.json").read_text(encoding="utf-8"))
FALLBACK_ICONS = {"chatgpt": "🤖", "youtube": "▶️", "canva": "🎨",
                  "spotify": "🎵", "capcut": "🎬", "claude": "✳️", "grok": "✖️"}
PRODUCTS = {
    item["id"]: dict(item, icon=FALLBACK_ICONS.get(item["id"], "▫️"),
                     product=item.get("product", item["name"]))
    for item in CONFIG["products"]
}


class TelegramAPI:
    def __init__(self, token):
        self.base_url = f"https://api.telegram.org/bot{token}/"

    def call(self, method, **data):
        request = urllib.request.Request(
            self.base_url + method,
            json.dumps(data).encode("utf-8"),
            {"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                result = json.load(response)
        except Exception as exc:
            print("Telegram API error:", type(exc).__name__)
            time.sleep(3)
            return None

        return result.get("result")


def button(text, callback):
    return {
        "text": text,
        "callback_data": callback,
    }


def home_keyboard():
    return {
        "keyboard": [
            [{"text": "🛍 المنتجات"}, {"text": "💬 الدعم"}],
            [{"text": "👛 المحفظة"}, {"text": "🔗 API"}],
            [{"text": "🛡 الضمان"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": "اختر من القائمة",
    }


def product_button(product_id, product):
    emoji_id = product.get("custom_emoji_id", "")
    if CONFIG.get("custom_icons_enabled") and emoji_id:
        return {"text": product["name"], "callback_data": f"product:{product_id}",
                "icon_custom_emoji_id": str(emoji_id)}
    return button(f"{product['icon']} {product['name']}", f"product:{product_id}")


def products_keyboard():
    rows = []

    items = list(PRODUCTS.items())

    for i in range(0, len(items), 3):
        row = []

        for product_id, product in items[i:i + 3]:
            row.append(
                product_button(product_id, product)
            )

        rows.append(row)

    rows.append([button("🏠 الرئيسية", "home")])

    return {"inline_keyboard": rows}


def send_message(api, chat_id, text, keyboard=None):
    data = {
        "chat_id": chat_id,
        "text": text,
    }

    if keyboard:
        data["reply_markup"] = keyboard

    api.call("sendMessage", **data)


def show_home(api, chat_id):
    text = (
        "👋 أهلاً بك في VEXA STORE\n\n"
        "🛍 متجر الخدمات الرقمية\n"
        "اختر القسم المطلوب من القائمة:"
    )

    send_message(
        api,
        chat_id,
        text,
        home_keyboard(),
    )
    show_products(api, chat_id)


def show_products(api, chat_id):
    send_message(
        api,
        chat_id,
        "🛍 المنتجات\n\nاختر الخدمة:",
        products_keyboard(),
    )


def show_product(api, chat_id, product_id):
    product = PRODUCTS.get(product_id)

    if not product:
        show_products(api, chat_id)
        return

    text = (
        f"{product['icon']} {product['name']}\n\n"
        f"📦 {product['product']}\n"
        f"📝 {product['description']}\n\n"
        "💰 السعر: سيتم تحديده\n"
        "الطلب غير مفعّل حاليًا"
    )

    keyboard = {
        "inline_keyboard": [
            [button("🛒 تفاصيل الطلب", f"buy:{product_id}")],
            [button("↩️ المنتجات", "products")],
            [button("🏠 الرئيسية", "home")],
        ]
    }

    send_message(api, chat_id, text, keyboard)


def handle_callback(api, query):
    api.call(
        "answerCallbackQuery",
        callback_query_id=query["id"],
    )

    message = query.get("message", {})
    if message.get("chat", {}).get("type") != "private":
        return
    handle_action(api, message["chat"]["id"], query.get("data", "home"))


def handle_action(api, chat_id, action):

    if action == "home":
        show_home(api, chat_id)

    elif action == "products":
        show_products(api, chat_id)

    elif action.startswith("product:"):
        product_id = action.split(":", 1)[1]
        show_product(api, chat_id, product_id)

    elif action.startswith("buy:"):
        product_id = action.split(":", 1)[1]
        product = PRODUCTS.get(product_id)

        if product:
            send_message(
                api,
                chat_id,
                f"🛒 طلب {product['name']}\n\n"
                "تم الوصول إلى صفحة الطلب.\n"
                "سيتم إضافة نظام الدفع لاحقاً.",
                {
                    "inline_keyboard": [
                        [
                            button(
                                "↩️ رجوع",
                                f"product:{product_id}",
                            )
                        ]
                    ]
                },
            )

    elif action == "support":
        send_message(
            api,
            chat_id,
            "💬 الدعم\n\nسيتم إضافة حساب الدعم هنا.",
            home_keyboard(),
        )

    elif action == "wallet":
        send_message(
            api,
            chat_id,
            "👛 المحفظة\n\nالمحفظة غير مفعّلة حاليًا.",
            home_keyboard(),
        )

    elif action == "api":
        send_message(
            api,
            chat_id,
            "🔗 API\n\nسيتم إضافة إعدادات API لاحقاً.",
            home_keyboard(),
        )

    elif action == "warranty":
        send_message(
            api,
            chat_id,
            "🛡 الضمان\n\nسيتم إضافة سياسة الضمان هنا.",
            home_keyboard(),
        )


MENU_ACTIONS = {"🛍 المنتجات": "products", "💬 الدعم": "support",
                "👛 المحفظة": "wallet", "🔗 API": "api", "🛡 الضمان": "warranty"}


def main():
    token = os.getenv("BOT_TOKEN")

    if not token:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    api = TelegramAPI(token)

    me = api.call("getMe")

    if not me:
        raise RuntimeError(
            "Unable to connect to Telegram."
        )

    print(
        f"Bot @{me.get('username')} is running..."
    )

    offset = 0

    while True:
        try:
            updates = api.call(
                "getUpdates",
                offset=offset,
                timeout=25,
                allowed_updates=[
                    "message",
                    "callback_query",
                ],
            )

            if not updates:
                continue

            for update in updates:
                offset = update["update_id"] + 1

                if "callback_query" in update:
                    handle_callback(
                        api,
                        update["callback_query"],
                    )

                elif "message" in update:
                    message = update["message"]

                    if (
                        message.get("chat", {}).get("type")
                        != "private"
                    ):
                        continue

                    chat_id = message["chat"]["id"]
                    text = message.get("text", "")

                    if text.startswith("/start"):
                        show_home(api, chat_id)
                    elif text.startswith("/products"):
                        show_products(api, chat_id)
                    elif text in MENU_ACTIONS:
                        handle_action(api, chat_id, MENU_ACTIONS[text])
                    else:
                        show_home(api, chat_id)

        except KeyboardInterrupt:
            print("Bot stopped.")
            break

        except Exception as exc:
            print("Error:", exc)
            time.sleep(3)


if __name__ == "__main__":
    main()
