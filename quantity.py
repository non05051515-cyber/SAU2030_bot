"""Quantity selection helpers for VEXA STORE."""

PRESET_QUANTITIES = (1, 2, 3, 5, 10)


def quantity_keyboard(product_id, back_callback):
    return {
        "inline_keyboard": [
            [
                {"text": str(q), "callback_data": f"qty:{product_id}:{q}"}
                for q in PRESET_QUANTITIES
            ],
            [{"text": "📦 شراء كل المتاح", "callback_data": f"qtyall:{product_id}"}],
            [{"text": "✍️ كمية مخصصة", "callback_data": f"qtycustom:{product_id}"}],
            [{"text": "↩️ رجوع", "callback_data": back_callback}],
        ]
    }


def quantity_text(product_name, unit_price=None, available=None):
    price = f"{unit_price} ر.س" if unit_price is not None else "حسب سعر المنتج"
    stock = f"\n📦 المتاح: <b>{available}</b>" if available is not None else ""
    return (
        "🧾 <b>اختيار الكمية</b>\n\n"
        f"🛒 المنتج: <b>{product_name}</b>\n"
        f"💵 سعر الوحدة: <b>{price}</b>{stock}\n\n"
        "اختر الكمية المطلوبة:"
    )


def order_summary(product_name, quantity, unit_price=None):
    total = f"{unit_price * quantity} ر.س" if unit_price is not None else "حسب سعر المنتج"
    return (
        "🧾 <b>ملخص الطلب</b>\n\n"
        f"🛒 المنتج: <b>{product_name}</b>\n"
        f"📦 الكمية: <b>{quantity}</b>\n"
        f"💰 الإجمالي: <b>{total}</b>"
    )


def parse_quantity(value):
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        return None
    return quantity if quantity > 0 else None
