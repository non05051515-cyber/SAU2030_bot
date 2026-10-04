"""Automatic Pandora -> VEXA catalogue sync for CapCut.\n\nDeployment marker: bundled with bot startup commit.

Runs outside Telegram callback handling so supplier latency never blocks the bot UI.
"""
import os
import re
import time
import urllib.parse
from decimal import Decimal, ROUND_HALF_UP

import storefront as s

CATEGORY_ID = "pandora_capcut"
CATEGORY_NAME = "CapCut"


def _slug(value):
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "")).strip("_").lower()
    return value[:48] or "item"


def _quote(endpoint, key, product_id, variant_id=""):
    payload = {"product_id": product_id, "quantity": 1}
    if variant_id:
        payload["variant_id"] = variant_id
    try:
        q = s._supplier_json_request(endpoint.rstrip("/") + "/quotes", key, "POST", payload, timeout=12)
        if q.get("can_purchase", False) and q.get("unit_price") is not None:
            return Decimal(str(q["unit_price"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception as exc:
        # A quote can legitimately be unavailable for an out-of-stock/temporarily
        # unavailable SKU. Catalogue sync must not treat that as a bot failure.
        code = getattr(exc, "code", "")
        print("CapCut quote unavailable:", product_id, type(exc).__name__, code, flush=True)
    return None


def _stock(source):
    try:
        value = s._pandora_stock_value(source)
        return max(0, int(value)) if value is not None else 1
    except Exception:
        return 1


def sync_capcut():
    endpoint = (os.getenv("PANDORA_API_BASE") or "https://api.pandoradigital.shop/api/v1").strip()
    key = (os.getenv("PANDORA_API_KEY") or "").strip()
    if not key:
        print("CapCut sync skipped: Pandora key missing", flush=True)
        return

    # Fetch the full Pandora catalogue page-by-page. Some accounts expose more
    # than the first page, so never assume /products?limit=100 is exhaustive.
    all_products = []
    seen_ids = set()
    page = 1
    while page <= 100:
        url = endpoint.rstrip("/") + "/products?" + urllib.parse.urlencode({"limit": 100, "page": page})
        payload = s._supplier_json_request(url, key, timeout=20)
        batch = s._pandora_list(payload)
        if not batch:
            break
        added = 0
        for item in batch:
            product_id = str(s._pandora_product_id(item) or "")
            marker = product_id or repr(item)
            if marker in seen_ids:
                continue
            seen_ids.add(marker)
            all_products.append(item)
            added += 1
        # Stop when Pandora repeats the same page, or explicitly reports no next page.
        if added == 0:
            break
        meta = payload if isinstance(payload, dict) else {}
        pagination = meta.get("pagination") or meta.get("meta") or {}
        has_next = pagination.get("has_next")
        if has_next is False:
            break
        next_page = pagination.get("next_page") or pagination.get("nextPage")
        if next_page:
            try:
                page = int(next_page)
                continue
            except Exception:
                pass
        if len(batch) < 100:
            break
        page += 1

    items = []
    for item in all_products:
        name = s._pandora_product_name(item)
        category = s._pandora_category_name(item)
        haystack = (name + " " + category).lower().replace(" ", "")
        if "capcut" in haystack or "capcutpro" in haystack:
            items.append(item)
    print("Pandora catalogue scan:", len(all_products), "total;", len(items), "CapCut matches", flush=True)
    if not items:
        print("CapCut sync: no Pandora products found", flush=True)
        return

    now = s.now_saudi()
    synced = 0
    with s.db() as conn:
        conn.execute("INSERT OR IGNORE INTO admin_categories(cid,name,created_at) VALUES (?,?,?)",
                     (CATEGORY_ID, CATEGORY_NAME, now))

    for item in items:
        product_id = s._pandora_product_id(item)
        product_name = s._pandora_product_name(item).strip()
        variants = s._pandora_variants(item) or [{"id": "", "name": ""}]
        raw_variants = item.get("variants") or item.get("options") or item.get("skus") or []
        if isinstance(raw_variants, dict):
            raw_variants = raw_variants.get("data") or raw_variants.get("items") or list(raw_variants.values())
        if not isinstance(raw_variants, list):
            raw_variants = []

        for variant in variants:
            variant_id = str(variant.get("id") or "")
            variant_name = str(variant.get("name") or "").strip()
            display = product_name
            if variant_name and variant_name.lower() not in ("default", product_name.lower()):
                display = product_name + " • " + variant_name
            pid = "pc_" + _slug(product_id + "_" + variant_id)
            source = item
            for rv in raw_variants:
                if isinstance(rv, dict) and str(rv.get("id") or rv.get("variant_id") or rv.get("variantId") or rv.get("sku") or "") == variant_id:
                    source = rv
                    break
            stock = _stock(source)
            available = 1 if stock > 0 else 0
            cost = _quote(endpoint, key, product_id, variant_id)

            with s.db() as conn:
                old = conn.execute("SELECT price_usd FROM admin_products WHERE pid=?", (pid,)).fetchone()
                price = str(cost if cost is not None else Decimal(str(old[0] if old and old[0] else "0")))
                desc = "CapCut عبر Pandora Digital. التسليم تلقائي بعد تأكيد الدفع."
                conn.execute("""INSERT INTO admin_products(pid,name,description,price_sar,available,created_at,category_id,price_usd,stock)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(pid) DO UPDATE SET name=excluded.name,description=excluded.description,
                    available=excluded.available,category_id=excluded.category_id,stock=excluded.stock""",
                    (pid, display, desc, str((Decimal(price or "0") * s.RATE).quantize(Decimal("0.01"))),
                     available, now, CATEGORY_ID, price, stock))
                conn.execute("""INSERT INTO supplier_api(pid,endpoint,api_key,service_id,enabled,provider,variant_id)
                    VALUES (?,?,?,?,1,'pandora',?)
                    ON CONFLICT(pid) DO UPDATE SET endpoint=excluded.endpoint,api_key=excluded.api_key,
                    service_id=excluded.service_id,enabled=1,provider='pandora',variant_id=excluded.variant_id""",
                    (pid, endpoint, '', product_id, variant_id))
                if cost is not None:
                    existing_margin = conn.execute("SELECT margin_usd FROM pandora_pricing WHERE pid=?", (pid,)).fetchone()
                    margin = existing_margin[0] if existing_margin else "0"
                    sale = (cost + Decimal(str(margin or "0"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    conn.execute("""INSERT INTO pandora_pricing(pid,supplier_cost_usd,margin_usd,updated_at)
                        VALUES (?,?,?,?) ON CONFLICT(pid) DO UPDATE SET supplier_cost_usd=excluded.supplier_cost_usd,updated_at=excluded.updated_at""",
                        (pid, str(cost), str(margin), now))
                    conn.execute("INSERT OR REPLACE INTO product_prices(pid,value,currency) VALUES (?,?,?)",
                                 (pid, str(sale), "USD"))
            synced += 1

    with s.db() as conn:
        cats = conn.execute("SELECT cid,name FROM admin_categories WHERE lower(name) LIKE '%capcut%' OR lower(name) LIKE '%cap cut%'").fetchall()
        diag = []
        for cat_id, cat_name in cats:
            total = conn.execute("SELECT COUNT(*) FROM admin_products WHERE category_id=?", (cat_id,)).fetchone()[0]
            linked = conn.execute("""SELECT COUNT(*) FROM admin_products p JOIN supplier_api a ON a.pid=p.pid
                WHERE p.category_id=? AND a.provider='pandora' AND a.enabled=1""", (cat_id,)).fetchone()[0]
            visible = conn.execute("""SELECT COUNT(*) FROM admin_products p LEFT JOIN product_visibility v ON v.pid=p.pid
                WHERE p.category_id=? AND COALESCE(v.visible,1)=1""", (cat_id,)).fetchone()[0]
            diag.append((cat_id, cat_name, total, linked, visible))
    print("CapCut category diagnostic:", diag, flush=True)
    print("CapCut Pandora sync complete:", synced, "products", flush=True)


def run():
    try:
        # Let the Telegram polling loop start first.
        time.sleep(3)
        sync_capcut()
    except Exception as exc:
        print("CapCut Pandora sync error:", type(exc).__name__, flush=True)

