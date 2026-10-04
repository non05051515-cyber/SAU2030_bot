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
        prices = c.execute('''SELECT p.pid,p.price_usd,pp.value,pp.currency,pr.supplier_cost_usd,pr.margin_usd
            FROM admin_products p LEFT JOIN product_prices pp ON pp.pid=p.pid
            LEFT JOIN pandora_pricing pr ON pr.pid=p.pid WHERE p.category_id='pandora_capcut' ''').fetchall()
        legacy_prices = c.execute("SELECT pid,value,currency FROM product_prices WHERE pid IN ('pd_14','pd_15','pd_16','pd_17')").fetchall()
    print('VEXA pricing metadata: '+json.dumps({'canonical':prices,'legacy':legacy_prices}),flush=True)
    import payment_execution
    routing = [{'order_id':r[0],'saved_pid':r[1],'resolved_pid':payment_execution.resolve_pid(s,r[1])}
               for r in orders if payment_execution.is_capcut(s,r[1])]
    print('VEXA CapCut routing: '+json.dumps(routing),flush=True)
    print('VEXA payment metadata: '+json.dumps({'products':products,'orders':orders},ensure_ascii=False),flush=True)
