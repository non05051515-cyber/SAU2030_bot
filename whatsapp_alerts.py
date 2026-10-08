"""Best-effort WhatsApp alerts for store orders (Meta WhatsApp Cloud API)."""
import json
import os
import threading
import urllib.request
import urllib.error


def _send(s, oid):
    token = os.getenv('WHATSAPP_ACCESS_TOKEN', '').strip()
    phone_number_id = os.getenv('WHATSAPP_PHONE_NUMBER_ID', '').strip()
    admin_to = os.getenv('WHATSAPP_ADMIN_TO', '').strip()
    api_version = os.getenv('WHATSAPP_GRAPH_API_VERSION', '').strip()
    template = os.getenv('WHATSAPP_ORDER_TEMPLATE', 'vexa_order_alert').strip()
    language = os.getenv('WHATSAPP_TEMPLATE_LANGUAGE', 'ar').strip()
    if not all((token, phone_number_id, admin_to, api_version, template, language)):
        return
    with s.db() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS whatsapp_order_alerts(
            order_id TEXT PRIMARY KEY, status TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        row = c.execute('''SELECT o.cid,o.pid,o.method,o.usd,o.sar,o.status,
            COALESCE(q.qty,1) FROM orders o LEFT JOIN quantity_snapshots q
            ON q.kind='order' AND q.key=o.id WHERE o.id=?''', (str(oid),)).fetchone()
        if not row:
            return
        claimed = c.execute('''INSERT OR IGNORE INTO whatsapp_order_alerts
            (order_id,status,updated_at) VALUES (?,?,?)''',
            (str(oid),'sending',s.now_saudi())).rowcount
        if not claimed:
            state = c.execute('SELECT status FROM whatsapp_order_alerts WHERE order_id=?',
                              (str(oid),)).fetchone()
            if state and state[0] in ('sending','sent'):
                return
            c.execute('UPDATE whatsapp_order_alerts SET status=?,updated_at=? WHERE order_id=?',
                      ('sending',s.now_saudi(),str(oid)))
    cid,pid,method,usd,sar,status,qty = row
    product = str(s.name(pid,cid))[:120]
    amount = (str(sar) + ' SAR / ' + str(usd) + ' USD')[:80]
    state_text = 'مدفوع' if status in ('paid','delivered') else ('مرفوض' if status == 'rejected' else 'بانتظار مراجعة الدفع')
    method_text = str(method)[:60]
    parameters = [str(oid),product,amount,method_text,state_text]
    body = {'messaging_product':'whatsapp','to':admin_to,'type':'template',
            'template':{'name':template,'language':{'code':language},
                        'components':[{'type':'body','parameters':[{'type':'text','text':x} for x in parameters]}]}}
    url = 'https://graph.facebook.com/'+api_version+'/'+phone_number_id+'/messages'
    req = urllib.request.Request(url,data=json.dumps(body,ensure_ascii=False).encode('utf-8'),
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=8) as response:
            ok = 200 <= response.status < 300
        with s.db() as c:
            c.execute('UPDATE whatsapp_order_alerts SET status=?,updated_at=? WHERE order_id=?',
                      ('sent' if ok else 'failed',s.now_saudi(),str(oid)))
    except Exception as exc:
        with s.db() as c:
            c.execute('UPDATE whatsapp_order_alerts SET status=?,updated_at=? WHERE order_id=?',
                      ('failed',s.now_saudi(),str(oid)))
        print('WhatsApp order alert failed:',type(exc).__name__,flush=True)


def notify(s, oid):
    """Run asynchronously so Meta delivery never delays the purchase flow."""
    threading.Thread(target=_send, args=(s,str(oid)), daemon=True).start()
