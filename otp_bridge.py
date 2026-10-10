"""Optional authenticated handoff from VEXA orders to its separate OTP bot."""
import json
import os
import re
import time
from urllib.request import Request, urlopen


def install(s):
    import email_codes
    original=email_codes.button

    def button(store,cid,oid):
        endpoint=os.getenv('OTP_SERVICE_URL','').strip().rstrip('/')
        username=os.getenv('OTP_BOT_USERNAME','').strip().lstrip('@')
        secret=os.getenv('OTP_BRIDGE_SECRET','')
        if not endpoint.startswith('https://') or not re.fullmatch(r'[A-Za-z0-9_]{5,32}',username) or len(secret)<32:
            return original(store,cid,oid)
        if not email_codes.eligible(store,cid,oid): return original(store,cid,oid)
        address=email_codes.account(store,oid)
        if not address:return original(store,cid,oid)
        with store.db() as c:
            has=c.execute("SELECT 1 FROM sqlite_master WHERE name='customer_subscriptions'").fetchone()
            expiry=c.execute('SELECT expires FROM customer_subscriptions WHERE order_id=?',(oid,)).fetchone() if has else None
        # The independent ticket is bounded, and customer identity is enforced there.
        expires=int(expiry[0]) if expiry else int(time.time())+29*86400
        data={'order_id':oid,'customer_id':cid,'email':address,'expires':expires,'limit':2}
        try:
            req=Request(endpoint+'/tickets',data=json.dumps(data).encode(),headers={'Authorization':'Bearer '+secret,'Content-Type':'application/json'})
            with urlopen(req,timeout=8) as r:token=json.load(r)['token']
            if not re.fullmatch(r'[A-Za-z0-9_-]{32}',token): raise ValueError('invalid_ticket')
        except Exception as exc:
            print('OTP handoff:',type(exc).__name__,flush=True)
            return original(store,cid,oid)
        result=store.btn(store.tr(cid,'الحصول على كود الدخول','Get login code'),'unused',store.ui_icon('ui_email_code'),style='success')
        result.pop('callback_data',None)
        result['url']='https://t.me/'+username+'?start='+token
        return result

    email_codes.button=button
