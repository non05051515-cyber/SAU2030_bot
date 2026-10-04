"""Admin-only product linking; credentials stay in environment, retail stays fixed."""
import json
import os
from decimal import Decimal


def local_products(s, api, cid, category=None):
    if cid != s.G['ADMIN_ID']:
        return
    if category is None:
        with s.db() as c:
            custom=c.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
        categories=list((k,v['name']) for k,v in s.G['PRODUCTS'].items())+custom
        rows=[[s.btn(label,'plcat:'+key)] for key,label in dict(categories).items()]
        title='🔗 ربط منتج موجود بـPandora\n\nاختر قسم المنتج في متجرك:'
    else:
        ids=list(dict.fromkeys(s.admin_category_product_ids(category)))
        rows=[[s.btn(s.name(pid,cid)+' | '+s.price(cid,pid,'USD'),'plpick:'+pid)] for pid in ids]
        title='اختر منتج متجرك، ثم اختر المنتج المقابل له من Pandora:'
    return s.send(api,cid,title,s.kb(rows+[[s.btn('↩️ لوحة الإدارة','admin')]]))


def refresh_cost(s,pid):
    """Refreshing supplier cost must never write a retail price."""
    cost=s.pandora_quote_cost(pid)
    if cost is None:return None
    with s.db() as c:
        c.execute('''INSERT INTO pandora_pricing(pid,supplier_cost_usd,margin_usd,updated_at)
            VALUES (?,?,'0',?) ON CONFLICT(pid) DO UPDATE SET
            supplier_cost_usd=excluded.supplier_cost_usd,updated_at=excluded.updated_at''',(pid,str(cost),s.now_saudi()))
    return cost


def editor(s,api,cid,pid):
    if cid!=s.G['ADMIN_ID']:return
    with s.db() as c:
        row=c.execute('SELECT service_id,variant_id,enabled,provider FROM supplier_api WHERE pid=?',(pid,)).fetchone()
    cost,margin,stamp=s.pandora_pricing_row(pid)
    sale=s.amount(pid,'USD')
    text='🔗 <b>'+s.esc(s.name(pid,cid))+'</b>\n'
    text+='الحالة: '+('✅ مربوط ومفعّل' if row and row[2] and row[3]=='pandora' else '⏸ غير مفعّل')+'\n'
    text+='سعر البيع الذي ضبطته: <b>'+s.esc(sale)+' USD</b>\n'
    text+='تكلفة Pandora: <b>'+s.esc(cost or 'لم تُحدّث بعد')+' USD</b>\n'
    if cost and sale is not None:
        profit=sale-Decimal(cost)
        text+='فرق السعر عن التكلفة: <b>'+s.esc(profit)+' USD</b>\n'
        if profit<0:text+='⚠️ سعر البيع أقل من تكلفة المورد. عدّل السعر قبل استقبال الطلبات.\n'
    text+='\nتحديث المخزون والتكلفة لا يغيّر سعر البيع اليدوي.'
    rows=[[s.btn('🔎 اختيار المنتج المقابل من Pandora','pandorabrowse:'+pid)],
          [s.btn('💵 تعديل سعر البيع','pricepick:'+pid),s.btn('➕ تحديد هامش الربح','suppliermargin:'+pid)],
          [s.btn('🔄 تحديث التكلفة والمخزون','plrefresh:'+pid)]]
    if row:rows.append([s.btn('⏸ إيقاف الربط' if row[2] else '✅ تفعيل الربط','pltoggle:'+pid)])
    rows.append([s.btn('↩️ ربط منتج آخر','admin:pandoralink'),s.btn('↩️ الإدارة','admin')])
    return s.send(api,cid,text,s.kb(rows))


def save(s,api,cid,pindex,vindex=None):
    if cid!=s.G['ADMIN_ID']:return
    with s.db() as c:
        row=c.execute("SELECT value FROM admin_state WHERE cid=? AND action='pandora_catalog'",(cid,)).fetchone()
    if not row:return local_products(s,api,cid)
    state=json.loads(row[0]);products=state.get('products',[])
    try:
        index=int(pindex)
        if index<0:raise ValueError()
        product=products[index];variants=product.get('variants') or []
        if variants and vindex is None:
            rows=[[s.btn(str(v.get('name') or v['id'])[:45],'pandorav:'+str(index)+':'+str(i))] for i,v in enumerate(variants)]
            return s.send(api,cid,'اختر نوع المنتج من Pandora:',s.kb(rows))
        if vindex is not None and int(vindex)<0:raise ValueError()
        variant=variants[int(vindex)]['id'] if variants else ''
        pid=state['pid']
    except (KeyError,ValueError,IndexError,TypeError):return s.pandora_catalog_categories(api,cid)
    if pid not in s.VARIANTS and pid not in s.G['PRODUCTS'] and not s.custom_product(pid):return
    # Environment-backed API access only; never copy the key into the database.
    with s.db() as c:
        c.execute('''INSERT INTO supplier_api(pid,service_id,variant_id,enabled,provider)
            VALUES (?,?,?,1,'pandora') ON CONFLICT(pid) DO UPDATE SET service_id=excluded.service_id,
            variant_id=excluded.variant_id,provider='pandora',enabled=1,api_key='',endpoint='' ''',
            (pid,str(product['id']),str(variant)))
        c.execute('DELETE FROM pandora_pricing WHERE pid=?',(pid,))
        c.execute('DELETE FROM admin_state WHERE cid=?',(cid,))
    s.pandora_sync_product(pid)
    try:refresh_cost(s,pid)
    except Exception:pass
    s.send(api,cid,'✅ تم ربط المنتج بـPandora وتفعيل التسليم التلقائي بعد قبول الدفع. سعر البيع محفوظ.')
    return editor(s,api,cid,pid)


def price_action(s,value):
    # The old CapCut admin price menu points to imported aliases. Edit the price
    # of the same active SKU shown in the customer catalogue instead.
    parts=value.split(':')
    if parts[0] in ('pricepick','priceedit') and len(parts)>1:
        import payment_execution
        pid=parts[-1]
        if payment_execution.is_capcut(s,pid):parts[-1]=payment_execution.resolve_pid(s,pid)
    return ':'.join(parts)


def action(s,api,cid,value):
    prefix,_,arg=value.partition(':')
    handled=value=='admin:pandoralink' or prefix in ('plcat','plpick','plrefresh','pltoggle','suppliermargin')
    if not handled:return False
    if cid!=s.G['ADMIN_ID']:return True
    if value=='admin:pandoralink':local_products(s,api,cid)
    elif prefix=='plcat':local_products(s,api,cid,arg)
    elif prefix=='plpick':s.pandora_catalog_open(api,cid,arg)
    elif prefix=='plrefresh':
        s.pandora_sync_product(arg)
        try:refresh_cost(s,arg)
        except Exception:s.send(api,cid,'تعذر تحديث تكلفة المورد الآن. سعر البيع لم يتغير.')
        editor(s,api,cid,arg)
    elif prefix=='pltoggle':
        with s.db() as c:c.execute("UPDATE supplier_api SET enabled=1-enabled WHERE pid=? AND provider='pandora' AND service_id<>''",(arg,))
        editor(s,api,cid,arg)
    elif prefix=='suppliermargin':
        with s.db() as c:c.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)',(cid,'supplier_margin',arg))
        s.send(api,cid,'أرسل هامش الربح بالدولار. سيصبح سعر البيع: تكلفة المورد + الهامش.',s.kb([[s.btn('إلغاء','supplierpick:'+arg)]]))
    return True


def restore_saved_capcut_prices(s):
    """One-time recovery of verified legacy retail prices; preserve later edits."""
    rows=(('pd_17','pc_prd_2ja9dtvu95-mwgfu','0.38','0.15'),
          ('pd_16','pc_prd_88qi7o6gb9gdyowp','2.00','1.29'))
    with s.db() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('CREATE TABLE IF NOT EXISTS price_recoveries (pid TEXT PRIMARY KEY, value TEXT NOT NULL)')
        for legacy,pid,saved,previous in rows:
            if c.execute('SELECT 1 FROM price_recoveries WHERE pid=?',(pid,)).fetchone():continue
            old=c.execute('SELECT value,currency FROM product_prices WHERE pid=?',(legacy,)).fetchone()
            current=c.execute('SELECT value,currency FROM product_prices WHERE pid=?',(pid,)).fetchone()
            mirror=c.execute('SELECT price_usd FROM admin_products WHERE pid=?',(pid,)).fetchone()
            if old!=(saved,'USD') or current!=(previous,'USD') or not mirror or mirror[0]!=previous:continue
            c.execute("UPDATE product_prices SET value=?,currency='USD' WHERE pid=?",(saved,pid))
            c.execute('UPDATE admin_products SET price_usd=?,price_sar=? WHERE pid=?',
                      (saved,str((Decimal(saved)*s.RATE).quantize(Decimal('0.01'))),pid))
            c.execute('INSERT INTO price_recoveries VALUES (?,?)',(pid,saved))
            print('VEXA recovered saved retail: '+pid+' = '+saved+' USD',flush=True)


def install(s):
    restore_saved_capcut_prices(s)
    old_can_order=s.can_order
    def can_order(pid):
        # Pandora supplier cost is informational only. It must never block a
        # customer order or change the manually configured VEXA retail price.
        return old_can_order(pid)
    s.can_order=can_order
    s.supplier_api_editor=lambda api,cid,pid:editor(s,api,cid,pid)
    s.pandora_catalog_product=lambda api,cid,index:save(s,api,cid,index)
    s.pandora_catalog_save=lambda api,cid,index,variant:save(s,api,cid,index,variant)
    # Existing refresh callers expect (cost, margin, sale); preserve the sale.
    def refresh(pid):
        cost=refresh_cost(s,pid)
        return (cost,Decimal(s.pandora_pricing_row(pid)[1] or '0'),s.amount(pid,'USD')) if cost is not None else None
    s.pandora_refresh_price=refresh
