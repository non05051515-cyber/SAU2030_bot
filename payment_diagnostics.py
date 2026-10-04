"""Allowlisted metadata only. Never reads supplier credentials or delivery data."""
import json

def report(s):
    with s.db() as c:
        products = c.execute("""SELECT p.pid,p.name,p.category_id,p.available,p.stock,
            COALESCE(a.provider,''),COALESCE(a.service_id,''),COALESCE(a.variant_id,''),COALESCE(a.enabled,0),COALESCE(v.visible,1)
            FROM admin_products p LEFT JOIN supplier_api a ON a.pid=p.pid
            LEFT JOIN product_visibility v ON v.pid=p.pid
            LEFT JOIN admin_categories cat ON cat.cid=p.category_id
            WHERE lower(p.name) LIKE '%capcut%' OR lower(COALESCE(cat.name,'')) LIKE '%capcut%'""").fetchall()
        orders = c.execute("""SELECT o.id,o.pid,o.status,o.method,o.created_at,
            COALESCE(a.provider,''),COALESCE(a.service_id,''),COALESCE(a.enabled,0),
            COALESCE(so.supplier_order_id,''),COALESCE(so.status,'')
            FROM orders o LEFT JOIN supplier_api a ON a.pid=o.pid
            LEFT JOIN supplier_orders so ON so.order_id=o.id
            ORDER BY o.rowid DESC LIMIT 15""").fetchall()
    print('VEXA payment metadata: '+json.dumps({'products':products,'orders':orders},ensure_ascii=False),flush=True)
