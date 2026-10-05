"""VEXA STORE Telegram bot — Arabic / English."""
import json,os,time,urllib.request,threading,http.client
import required_group
import customer_inbox
import telegram_payments
from pathlib import Path
BASE=Path(__file__).resolve().parent;CONFIG=json.loads((BASE/'catalog.json').read_text(encoding='utf-8'));ADMIN_ID=8386371522
USERS_FILE=Path('/data/users.json');LANG_FILE=Path('/data/languages.json');PENDING_RECEIPTS={};PENDING_ADMIN_DELIVERY={};PAYMENT_REVIEWS={}
ICONS={'chatgpt':'🤖','youtube':'▶️','canva':'🎨','gemini':'✨','capcut':'🎬','claude':'✳️','grok':'✖️','netflix':'📺','iptv':'📡'}
PRODUCTS={x['id']:dict(x,icon=ICONS.get(x['id'],'▫️')) for x in CONFIG['products']}
EN={'chatgpt':('ChatGPT','AI assistant for conversations, writing, and everyday tasks.'),'youtube':('YouTube','Enjoy YouTube without ads, background playback, offline downloads, and YouTube Music Premium benefits.'),'canva':('Canva','A design platform for creating graphics and visual content.'),'gemini':('Gemini','AI assistant for conversations, writing, and content analysis.'),'capcut':('CapCut','Video editing and content creation tool.'),'claude':('Claude','Choose the Claude product that suits you.'),'grok':('Grok','AI assistant for conversations and answering questions.'),'netflix':('NETFLIX','Netflix subscription for movies, series, and entertainment content.'),'iptv':('IPTV','IPTV subscriptions for watching content on supported devices.')}
CAPCUT={'capcut_6m':('CapCut Pro — حساب فردي لمدة 6 أشهر','CapCut Pro — Individual Account for 6 Months','46.88','حساب فردي لمدة 6 أشهر.\nضمان لمدة 3 أشهر.\nحساب مستقر للغاية.','Individual account for 6 months.\n3-month warranty.\nHighly stable account.'),'capcut_1m_1600':('CapCut Pro — شهر + 1600 وحدة ائتمان','CapCut Pro — 1 Month + 1600 Credits','13.69','اشتراك CapCut Pro لمدة شهر مع 1600 وحدة ائتمان.\nضمان كامل.','CapCut Pro for one month with 1600 credits.\nFull warranty.'),'capcut_1m':('CapCut Pro — شهر واحد','CapCut Pro — 1 Month','11.21','حساب Pro خاص لمدة 30 يومًا.\nيمكن استخدامه على جهازين كحد أقصى، مع ضمان كامل.\nيتم تسجيل الدخول أولًا إلى تطبيق الهاتف، ثم تطبيق سطح المكتب عبر QR.','Private Pro account for 30 days.\nCan be used on up to 2 devices with full warranty.\nSign in on the mobile app first, then the desktop app using QR.'),'capcut_7d':('CapCut Pro — 7 أيام','CapCut Pro — 7 Days','6.38','حساب CapCut لمدة 7 أيام.\nيمكن استخدامه على جهازين كحد أقصى، مع ضمان كامل.\nتتوفر طلبات مسبقة.','CapCut account for 7 days.\nCan be used on up to 2 devices with full warranty.\nPre-orders are available.')}
CLAUDE={'claude_pro':('إعادة شحن Claude Pro الرسمية لمدة شهر','Official Claude Pro Recharge — 1 Month'),'claude_api_500m':('Claude API — 500 مليون توكن — 6 أيام','Claude API — 500M Tokens — 6 Days'),'claude_api_100m':('Claude API — 100 مليون توكن — 3 أيام','Claude API — 100M Tokens — 3 Days'),'claude_api_50m':('Claude API — 50 مليون توكن — يومان','Claude API — 50M Tokens — 2 Days'),'claude_api_10m':('Claude API — 10 ملايين توكن — يوم واحد','Claude API — 10M Tokens — 1 Day')}
def jload(p,d):
 try:return json.loads(p.read_text(encoding='utf-8'))
 except:return d
LANGS=jload(LANG_FILE,{})
_USERS_CACHE=set(jload(USERS_FILE,[]))
_USERS_LOCK=threading.Lock()
def save_user(cid):
 if cid in _USERS_CACHE:return
 with _USERS_LOCK:
  if cid in _USERS_CACHE:return
  _USERS_CACHE.add(cid)
  try:USERS_FILE.write_text(json.dumps(sorted(_USERS_CACHE)),encoding='utf-8')
  except:pass
def lang(cid):return LANGS.get(str(cid),'ar')
def set_lang(cid,v):
 LANGS[str(cid)]=v
 try:LANG_FILE.write_text(json.dumps(LANGS),encoding='utf-8')
 except:pass
def tr(cid,a,e):return e if lang(cid)=='en' else a
def btn(t,c):return {'text':t,'callback_data':c}
class API:
 def __init__(self,t):
  self.t=t;self.u=f'https://api.telegram.org/bot{t}/';self._errors=threading.local();self._connections=threading.local()
 @property
 def last_error(self):return getattr(self._errors,'value',None)
 def _conn(self):
  conn=getattr(self._connections,'telegram',None)
  if conn is None:
   conn=http.client.HTTPSConnection('api.telegram.org',timeout=40)
   self._connections.telegram=conn
  return conn
 def _reset_conn(self):
  conn=getattr(self._connections,'telegram',None)
  if conn:
   try:conn.close()
   except Exception:pass
  self._connections.telegram=None
 def call(self,m,**d):
  self._errors.value=None
  body=json.dumps(d).encode()
  detail=''
  try:
   conn=self._conn()
   conn.request('POST',f'/bot{self.t}/'+m,body,{'Content-Type':'application/json','Connection':'keep-alive'})
   r=conn.getresponse();raw=r.read()
   payload=json.loads(raw.decode('utf-8','replace')) if raw else {}
   if 200 <= r.status < 300 and payload.get('ok'):
    return payload.get('result')
   detail=raw.decode('utf-8','replace')
   self._errors.value={'code':r.status,'description':payload.get('description',''),'retry_after':payload.get('parameters',{}).get('retry_after')}
   print('Telegram API error:',m,r.status,detail[:300],flush=True)
  except Exception as e:
   self._reset_conn()
   # Retry once on a fresh connection; stale keep-alive sockets are harmless.
   try:
    conn=self._conn()
    conn.request('POST',f'/bot{self.t}/'+m,body,{'Content-Type':'application/json','Connection':'keep-alive'})
    r=conn.getresponse();raw=r.read()
    payload=json.loads(raw.decode('utf-8','replace')) if raw else {}
    if 200 <= r.status < 300 and payload.get('ok'):
     return payload.get('result')
    detail=raw.decode('utf-8','replace')
    self._errors.value={'code':r.status,'description':payload.get('description',''),'retry_after':payload.get('parameters',{}).get('retry_after')}
   except Exception as retry_error:
    self._reset_conn()
    self._errors.value={'code':None,'description':str(retry_error)}
    print('Telegram API error:',m,type(retry_error).__name__,repr(retry_error),flush=True)
def send(a,c,t,k=None):
 d={'chat_id':c,'text':t,'parse_mode':'HTML'}
 if k:d['reply_markup']=k
 return a.call('sendMessage',**d)
def keyboard(c):
 en=lang(c)=='en';return {'keyboard':[[{'text':'🚀 Start' if en else '🚀 ابدأ'},{'text':'🛍 Products' if en else '🛍 المنتجات'},{'text':'💬 Support' if en else '💬 الدعم'}],[{'text':'👛 Wallet' if en else '👛 المحفظة'},{'text':'🔗 API'}],[{'text':'🛡 Warranty' if en else '🛡 الضمان'},{'text':'🌐 Language' if en else '🌐 اللغة'}]],'resize_keyboard':True,'is_persistent':True}
def show_start(a,c):send(a,c,'👋 <b>Welcome to VEXA STORE | مرحباً بك في VEXA STORE</b>\n\n🌐 Choose your language | اختر لغتك',{'inline_keyboard':[[btn('🇸🇦 العربية','lang:ar'),btn('🇺🇸 English','lang:en')]]})
def show_language(a,c):send(a,c,'🌐 <b>Choose your language | اختر لغتك</b>',{'inline_keyboard':[[btn('🇸🇦 العربية','lang:ar'),btn('🇺🇸 English','lang:en')]]})
def pname(c,pid):return EN.get(pid,(PRODUCTS.get(pid,{}).get('name',pid),''))[0] if lang(c)=='en' else PRODUCTS.get(pid,{}).get('name',pid)
def show_products(a,c):
 rows=[];items=list(PRODUCTS.items())
 for i in range(0,len(items),3):
  row=[]
  for pid,p in items[i:i+3]:
   b=btn(pname(c,pid),f'product:{pid}');eid=p.get('custom_emoji_id')
   if CONFIG.get('custom_icons_enabled') and eid:b['icon_custom_emoji_id']=str(eid)
   row.append(b)
  rows.append(row)
 rows.append([btn(tr(c,'🏠 الرئيسية','🏠 Home'),'home')]);send(a,c,tr(c,'🛍 <b>المنتجات</b>\n\nاختر الخدمة:','🛍 <b>Products</b>\n\nChoose a service:'),{'inline_keyboard':rows})
def show_home(a,c):send(a,c,tr(c,'👋 أهلاً بك في <b>VEXA STORE</b>\n\n🛍 متجر الخدمات الرقمية\nاختر القسم المطلوب.\n\n💳 <b>لشحن النقاط والدعم:</b> @m7mmd2030_1','👋 Welcome to <b>VEXA STORE</b>\n\n🛍 Digital services store\nChoose a section.\n\n💳 <b>Top-ups & support:</b> @m7mmd2030_1'),keyboard(c));show_products(a,c)
def show_product(a,c,pid):
 p=PRODUCTS.get(pid)
 if not p:return show_products(a,c)
 if pid=='capcut':
  rows=[[btn(f"{x[1] if lang(c)=='en' else x[0]} — {x[2]} SAR",f'capcut:{k}')] for k,x in CAPCUT.items()];rows.append([btn(tr(c,'↩️ العودة إلى المنتجات','↩️ Back to Products'),'products')]);return send(a,c,tr(c,'🎬 <b>CapCut Pro | كاب كات برو</b>\n\nاختر المنتج المطلوب 👇','🎬 <b>CapCut Pro</b>\n\nChoose a product 👇'),{'inline_keyboard':rows})
 if pid=='claude':
  rows=[[btn(x[1] if lang(c)=='en' else x[0],f'claude:{k}')] for k,x in CLAUDE.items()];rows.append([btn(tr(c,'↩️ العودة إلى المنتجات','↩️ Back to Products'),'products')]);return send(a,c,tr(c,'✳️ <b>Claude | كلود</b>\n\nاختر المنتج المطلوب 👇','✳️ <b>Claude</b>\n\nChoose a product 👇'),{'inline_keyboard':rows})
 if pid=='chatgpt':return send(a,c,tr(c,'🤖 <b>ChatGPT Plus | شات جي بي تي بلس</b>\n\nاختر نوع الاشتراك المناسب لك.','🤖 <b>ChatGPT Plus</b>\n\nChoose your subscription type.'),{'inline_keyboard':[[btn(tr(c,'🔐 بلس شهر • حساب خاص','🔐 Plus 1 Month • Private Account'),'chatgpt_private')],[btn(tr(c,'📧 بلس شهر • على إيميلك','📧 Plus 1 Month • Your Email'),'chatgpt_email')],[btn(tr(c,'↩️ العودة إلى المنتجات','↩️ Back to Products'),'products')]]})
 en=lang(c)=='en';name=EN.get(pid,(p['name'],''))[0] if en else p['name'];desc=EN.get(pid,('',p.get('description','')))[1] if en else p.get('description','');price=(f"{p['price']} {'SAR' if en else p.get('currency','ر.س')}" if p.get('price') is not None else tr(c,'يُضاف لاحقاً','Coming soon'))
 send(a,c,f"{p['icon']} <b>{name}</b>\n💵 {tr(c,'السعر','Price')}: <b>{price}</b>\n\n💦 <b>{tr(c,'الوصف','Description')}:</b>\n{desc}",{'inline_keyboard':[[btn(tr(c,'🛒 شراء الآن','🛒 Buy Now'),f'buy:{pid}')],[btn(tr(c,'💬 الدعم الفني','💬 Support'),'support')],[btn(tr(c,'↩️ العودة إلى المنتجات','↩️ Back to Products'),'products')]]})
def show_capcut(a,c,pid):
 x=CAPCUT.get(pid)
 if not x:return show_product(a,c,'capcut')
 en=lang(c)=='en';send(a,c,f"🎬 <b>{x[1] if en else x[0]}</b>\n\n💵 {tr(c,'السعر','Price')}: <b>{x[2]} SAR</b>\n\n💦 <b>{tr(c,'الوصف','Description')}:</b>\n{x[4] if en else x[3]}",{'inline_keyboard':[[btn(tr(c,'🛒 شراء الآن','🛒 Buy Now'),f'buy:{pid}')],[btn(tr(c,'↩️ رجوع إلى CapCut','↩️ Back to CapCut'),'product:capcut')]]})
def show_claude(a,c,pid):
 x=CLAUDE.get(pid)
 if not x:return show_product(a,c,'claude')
 en=lang(c)=='en';title=x[1] if en else x[0]
 if pid=='claude_pro':body=tr(c,'رمز تفعيل رسمي (CDK) لاشتراك Claude Pro لمدة شهر. يجب ألا يكون لديك اشتراك نشط أو فواتير غير مدفوعة. الضمان للاشتراك فقط ولا يشمل حظر الحساب.','Official CDK activation for one month of Claude Pro. Your account must not have an active subscription or unpaid invoices. Warranty covers the subscription only and does not cover account bans.')
 else:body=tr(c,'متوافق مع Claude Dev وCursor وVS Code و9router وغيرها.','Compatible with Claude Dev, Cursor, VS Code, 9router and more.')
 send(a,c,f'✳️ <b>{title}</b>\n\n{body}',{'inline_keyboard':[[btn(tr(c,'🛒 طلب المنتج','🛒 Order Product'),f'buy:{pid}')],[btn(tr(c,'↩️ رجوع إلى Claude','↩️ Back to Claude'),'product:claude')]]})
def order_name(c,pid):
 if pid in CAPCUT:return CAPCUT[pid][1 if lang(c)=='en' else 0]
 if pid in CLAUDE:return CLAUDE[pid][1 if lang(c)=='en' else 0]
 if pid=='chatgpt_private':return tr(c,'ChatGPT Plus — حساب خاص','ChatGPT Plus — Private Account')
 if pid=='chatgpt_email':return tr(c,'ChatGPT Plus — على إيميلك','ChatGPT Plus — Your Email')
 return pname(c,pid)
def back(pid):
 for x in ('chatgpt','capcut','claude'):
  if pid.startswith(x+'_'):return 'product:'+x
 return 'product:'+pid
def pv(n):return os.getenv(n,'Not configured')
def show_payment(a,c,pid):
 bank={'text':tr(c,'تحويل بنكي — الراجحي','Bank Transfer — Al Rajhi'),'callback_data':f'paybank:{pid}','icon_custom_emoji_id':'5452024291472196683'};by={'text':'USDT — Bybit','callback_data':f'paybybit:{pid}','icon_custom_emoji_id':'5472387796574418157'};send(a,c,f"💳 <b>{tr(c,'اختر طريقة الدفع','Choose Payment Method')}</b>\n\n🛒 {tr(c,'المنتج','Product')}: <b>{order_name(c,pid)}</b>",{'inline_keyboard':[[bank],[by],[btn(tr(c,'↩️ رجوع','↩️ Back'),back(pid))]]})
def show_bank(a,c,pid):send(a,c,tr(c,f"🏦 <b>التحويل البنكي — مصرف الراجحي</b>\n\nاسم الحساب: <b>{pv('PAYMENT_BANK_HOLDER')}</b>\nرقم الحساب:\n<code>{pv('PAYMENT_BANK_ACCOUNT')}</code>\n\nالآيبان:\n<code>{pv('PAYMENT_BANK_IBAN')}</code>\n\n⚠️ تأكد من البيانات قبل التحويل.",f"🏦 <b>Bank Transfer — Al Rajhi</b>\n\nAccount holder: <b>{pv('PAYMENT_BANK_HOLDER')}</b>\nAccount number:\n<code>{pv('PAYMENT_BANK_ACCOUNT')}</code>\n\nIBAN:\n<code>{pv('PAYMENT_BANK_IBAN')}</code>\n\n⚠️ Verify the details before transferring."),{'inline_keyboard':[[btn(tr(c,'🟢 تم التحويل','🟢 Transfer Complete'),f'receipt:bank:{pid}')],[btn(tr(c,'↩️ طرق الدفع','↩️ Payment Methods'),f'buy:{pid}')]]})
def show_bybit(a,c,pid):send(a,c,tr(c,'🪙 <b>USDT — Bybit</b>\n\nاختر طريقة إرسال USDT:','🪙 <b>USDT — Bybit</b>\n\nChoose how to send USDT:'),{'inline_keyboard':[[btn('🟡 Bybit Pay',f'bybitid:{pid}')],[btn('🔴 USDT • TRON (TRC20)',f'trc20:{pid}')],[btn('🟡 USDT • BSC (BEP20)',f'bep20:{pid}')],[btn(tr(c,'↩️ طرق الدفع','↩️ Payment Methods'),f'buy:{pid}')]]})
def show_crypto(a,c,pid,kind):
 key={'bybitid':'PAYMENT_BYBIT_PAY_ID','trc20':'PAYMENT_USDT_TRC20','bep20':'PAYMENT_USDT_BEP20'}[kind];send(a,c,f"🪙 <b>USDT</b>\n\n{'Bybit Pay ID' if kind=='bybitid' else 'Wallet Address'}:\n<code>{pv(key)}</code>\n\n⚠️ {tr(c,'تأكد من الشبكة والمعرف قبل الإرسال.','Verify the network and address/ID before sending.')}",{'inline_keyboard':[[btn(tr(c,'🟢 تم التحويل','🟢 Transfer Complete'),f'receipt:{kind}:{pid}')],[btn('↩️ Bybit',f'paybybit:{pid}')]]})
def receipt(a,c,pid,m):PENDING_RECEIPTS[c]={'product':pid,'method':m};send(a,c,tr(c,'📸 <b>إرسال إثبات الدفع</b>\n\nأرسل الآن صورة إثبات التحويل.','📸 <b>Send Payment Proof</b>\n\nSend a photo of your payment receipt now.'))
def handle_receipt(a,m):
 c=m['chat']['id'];x=PENDING_RECEIPTS.get(c)
 if not x:return False
 if not m.get('photo'):send(a,c,tr(c,'📸 فضلاً أرسل صورة إثبات الدفع.','📸 Please send a photo of the payment receipt.'));return True
 u=m.get('from',{});review_id=str(int(time.time()*1000));PAYMENT_REVIEWS[review_id]={'customer':c,'product':x['product'],'method':x['method'],'lang':lang(c)}
 send(a,ADMIN_ID,f"🧾 <b>إثبات دفع جديد</b>\n🛒 {order_name(c,x['product'])}\n👤 {'@'+u['username'] if u.get('username') else 'بدون معرف'}\n🆔 <code>{c}</code>")
 a.call('forwardMessage',chat_id=ADMIN_ID,from_chat_id=c,message_id=m['message_id'])
 send(a,ADMIN_ID,'اختر الإجراء لهذا الطلب:',{'inline_keyboard':[[btn('✅ قبول',f'adminpay:accept:{review_id}'),btn('❌ رفض',f'adminpay:reject:{review_id}')]]})
 PENDING_RECEIPTS.pop(c,None);send(a,c,tr(c,'✅ تم استلام إثبات الدفع وإرساله للإدارة للمراجعة.','✅ Payment proof received and sent for review.'),keyboard(c));return True

def handle_admin_delivery(a,m):
 if m.get('chat',{}).get('id')!=ADMIN_ID:return False
 x=PENDING_ADMIN_DELIVERY.get(ADMIN_ID)
 if not x:return False
 customer=x['customer']
 if x.get('order_id') and payment_execution.guard_delivery(storefront,x['order_id']):
  PENDING_ADMIN_DELIVERY.pop(ADMIN_ID,None)
  send(a,ADMIN_ID,'⚠️ التسليم اليدوي محظور لطلبات Pandora.')
  return True
 if x.get('review_id'):
  PENDING_ADMIN_DELIVERY.pop(ADMIN_ID,None)
  send(a,ADMIN_ID,'⚠️ يجب اعتماد الطلب من لوحة الطلبات قبل التسليم.')
  return True
 try:
  a.call('copyMessage',chat_id=customer,from_chat_id=ADMIN_ID,message_id=m['message_id'])
  if x.get('order_id'):
   try:
    with storefront.db() as conn:
     conn.execute('UPDATE orders SET status="delivered" WHERE id=?', (x['order_id'],))
   except Exception:
    pass
  send(a,ADMIN_ID,'✅ تم إرسال الطلب للعميل وتسجيله كمُسلّم.')
 except Exception:
  send(a,ADMIN_ID,'❌ تعذر إرسال الطلب للعميل. بقي الطلب بانتظار التسليم.')
 PENDING_ADMIN_DELIVERY.pop(ADMIN_ID,None)
 return True
def action(a,c,x):
 if x.startswith('adminpay:') and c==ADMIN_ID:
  _,decision,rid=x.split(':',2)
  return payment_execution.legacy_review(storefront,a,c,decision,rid)
 if x.startswith('lang:'):
  set_lang(c,x.split(':',1)[1]);show_home(a,c)
 elif x in ('home','enter_store'):show_home(a,c)
 elif x=='start':show_start(a,c)
 elif x=='products':show_products(a,c)
 elif x=='language':show_language(a,c)
 elif x.startswith('product:'):show_product(a,c,x.split(':',1)[1])
 elif x.startswith('capcut:'):show_capcut(a,c,x.split(':',1)[1])
 elif x.startswith('claude:'):show_claude(a,c,x.split(':',1)[1])
 elif x in ('chatgpt_private','chatgpt_email'):send(a,c,f"🤖 <b>{order_name(c,x)}</b>\n\n{tr(c,'اشتراك Plus لمدة شهر. التفعيل بعد إتمام الطلب.','Plus subscription for one month. Activation after completing the order.')}",{'inline_keyboard':[[btn(tr(c,'🛒 طلب المنتج','🛒 Order Product'),f'buy:{x}')],[btn(tr(c,'↩️ رجوع','↩️ Back'),'product:chatgpt')]]})
 elif x.startswith('buy:'):PENDING_RECEIPTS.pop(c,None);show_payment(a,c,x.split(':',1)[1])
 elif x.startswith('paybank:'):show_bank(a,c,x.split(':',1)[1])
 elif x.startswith('paybybit:'):show_bybit(a,c,x.split(':',1)[1])
 elif x.startswith(('bybitid:','trc20:','bep20:')):k,p=x.split(':',1);show_crypto(a,c,p,k)
 elif x.startswith('receipt:'):_,m,p=x.split(':',2);receipt(a,c,p,m)
 elif x=='support':send(a,c,'Support:@m7mmd2030_1',keyboard(c))
 elif x=='wallet':send(a,c,tr(c,'👛 المحفظة غير مفعّلة حاليًا.','👛 Wallet is currently unavailable.'),keyboard(c))
 elif x=='api':send(a,c,tr(c,'🔗 سيتم إضافة إعدادات API لاحقاً.','🔗 API settings will be added later.'),keyboard(c))
 elif x=='warranty':send(a,c,tr(c,'🛡 سيتم إضافة سياسة الضمان هنا.','🛡 Warranty policy will be added here.'),keyboard(c))
MENU={'💎 الإحالات':'referrals','💎 Referrals':'referrals','🚀 ابدأ':'start','🚀 Start':'start','🛍 المنتجات':'products','🛍 Products':'products','💬 الدعم':'support','💬 Support':'support','👛 المحفظة':'wallet','👛 Wallet':'wallet','🔗 API':'api','🛡 الضمان':'warranty','🛡 Warranty':'warranty','🌐 اللغة':'language','🌐 Language':'language'}
def _run_embedded_capcut_sync():
 import pandora_catalog_sync
 pandora_catalog_sync.run()

def main():
 t=os.getenv('BOT_TOKEN')
 if not t:raise RuntimeError('BOT_TOKEN missing')
 a=API(t)
 # This bot uses getUpdates polling; remove any stale webhook first.
 a.call('deleteWebhook',drop_pending_updates=False)
 a.call('setMyCommands',commands=[{'command':'start','description':'Start | ابدأ'},{'command':'products','description':'Products | المنتجات'},{'command':'currency','description':'Currency | العملة'},{'command':'language','description':'Language | اللغة'}])
 a.call('setChatMenuButton',menu_button={'type':'commands'})
 if 'broadcast_new_products' in globals():broadcast_new_products(a)
 # Keep maintenance/broadcast work off the message polling path.
 # One worker preserves the original ordering and prevents overlapping ticks.
 def maintenance_loop():
  # Keep all background services, but avoid hammering SQLite every 2 seconds.
  jobs=[('pandora_stock_notifications',pandora_stock_notifications.tick,30),('supplier_orders',globals().get('tick_supplier_orders'),15),('customer_activity',storefront.tick_customer_activity,2),('stock_alerts',globals().get('tick_stock_alerts'),5),('channel_catalog',globals().get('tick_channel_catalog'),5),('auto_ads',globals().get('tick_auto_ads'),10),('product_broadcast',globals().get('tick_product_broadcast'),5)]
  last={}
  while True:
   now=time.monotonic()
   for name, fn, interval in jobs:
    if fn and now-last.get(name,0)>=interval:
     last[name]=now
     try:fn(a)
     except Exception as e:print('Maintenance error',name,type(e).__name__,str(e),flush=True)
   time.sleep(0.5)
 import threading
 threading.Thread(target=maintenance_loop,name='bot-maintenance',daemon=True).start()
 threading.Thread(target=_run_embedded_capcut_sync,daemon=True).start()
 import payment_diagnostics
 payment_diagnostics.report(storefront)
 print('VEXA callback handler:', action.__module__ + '.' + action.__qualname__, flush=True)
 offset=0;print('Bot running...',flush=True)
 last_poll_log=0
 while True:
  try:
   updates=a.call('getUpdates',offset=offset,timeout=25,allowed_updates=['message','callback_query','pre_checkout_query'])
   if updates is None:
    print('Polling failed; retrying',flush=True)
    time.sleep(2)
    continue
   if updates or time.time()-last_poll_log>120:
    print('Polling alive; updates:',len(updates),'offset:',offset,flush=True)
    last_poll_log=time.time()
   for u in updates:
    offset=u['update_id']+1
    if 'pre_checkout_query' in u:
     telegram_payments.precheckout(a,u['pre_checkout_query']);continue
    if 'callback_query' in u:
     q=u['callback_query']
     # Acknowledge the Telegram button press without blocking the actual action.
     # This removes one full network round-trip from every button click.
     threading.Thread(target=a.call,args=('answerCallbackQuery',),kwargs={'callback_query_id':q['id']},daemon=True).start()
     c=q.get('message',{}).get('chat',{}).get('id')
     if c:
      save_user(c)
      data=q.get('data','home')
      payment_execution.EVENT.message_id=q.get('message',{}).get('message_id')
      if customer_inbox.action(a,c,data):continue
      if telegram_payments.action(a,c,data):continue
      storefront.track_customer_activity(q.get('from', {}).get('id'), value=data)
      if data in ('ads:stop','ads:resume'):
       action(a,c,data);continue
      if data=='required_group:verify':
       if required_group.verify(a,c) and not channel_catalog.resume(a,c):show_start(a,c)
      elif c==ADMIN_ID or required_group.approved(c):action(a,c,data)
      else:required_group.prompt(a,c)
     continue
    m=u.get('message',{});c=m.get('chat',{}).get('id')
    if not c or m.get('chat',{}).get('type')!='private':continue
    save_user(c);txt=m.get('text','')
    if telegram_payments.paid(a,m):continue
    customer_inbox.capture(m)
    storefront.track_customer_activity(m.get('from', {}).get('id'), message=m)
    if txt.startswith('/start'):channel_catalog.remember(c,txt)
    if c!=ADMIN_ID and not required_group.approved(c):
     if txt.startswith('/start'):
      required_group.prompt(a,c)
     else:required_group.prompt(a,c)
     continue
    if txt.startswith('/start') and channel_catalog.resume(a,c):continue
    if pandora_stock_notifications.import_message(a,m):continue
    if handle_admin_delivery(a,m):continue
    if customer_inbox.handle(a,m):continue
    if 'handle_info_icon' in globals() and handle_info_icon(a,m):continue
    if 'handle_info_warranty' in globals() and handle_info_warranty(a,m):continue
    if 'handle_admin_product' in globals() and handle_admin_product(a,m):continue
    if handle_receipt(a,m):continue
    if txt.startswith('/start'):
     parts=txt.split(maxsplit=1)
     if len(parts)>1 and parts[1].startswith('ref_') and 'register_referral' in globals():register_referral(c,parts[1][4:])
     show_start(a,c)
    elif txt=='/pandoraicons' and c==ADMIN_ID:pandora_stock_notifications.begin_import(a,c)
    elif txt.startswith('/products'):show_products(a,c)
    elif txt.startswith('/currency'):action(a,c,'settings:currency')
    elif txt.startswith('/language'):action(a,c,'settings:lang')
    elif txt in MENU:action(a,c,MENU[txt])
    else:show_home(a,c)
  except KeyboardInterrupt:break
  except Exception as e:print('Error:',e);time.sleep(3)
import storefront
from storefront import install
install(globals())

# Load optional store extensions here too, so they work even when the host
# starts bot.py directly instead of using runner.py.
import iptv_extension
import broadcast_admin
import chatgpt_extension
# IPTV installs its category helpers when imported; preserve its callback
# handler before adding the admin and ChatGPT handlers.
action = iptv_extension.action
handle_action = action
broadcast_admin.install(globals())
chatgpt_extension.install(globals())
tick_auto_ads = broadcast_admin.tick_auto_ads
tick_product_broadcast = broadcast_admin.tick_product_broadcast

import channel_catalog
channel_catalog.install(globals())
import product_options
product_options.install(storefront, globals())

import welcome_editor
welcome_editor.install(globals())

import payment_execution
payment_execution.install(storefront, globals())

import pandora_stock_notifications

import product_announcements
product_announcements.install(globals())

if __name__=='__main__':main()

