import email.message
import base64
import os
from email.utils import formatdate
import time
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import test_payment_execution as fixtures
import email_codes as otp


class EmailCodes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.Payments.setUpClass()
    @classmethod
    def tearDownClass(cls):fixtures.Payments.tearDownClass()
    def setUp(self):
        self.p=fixtures.Payments('test_unauthorized_callback');self.p.setUp()
        self.s=self.p.s;self.api=self.p.api;self.bot=self.p.bot;self.admin=self.p.admin
        self.bot.action(self.api,self.admin,'emailcodetoggle:pc_2')
        self.oid=self.p.order('pc_2',status='delivered')
        otp.bind_delivery(self.s,self.oid,'user@example.com | fakepassword')
        self.mailboxes=patch.object(otp,'mailbox_configs',return_value=[{'user':'inbox@gmail.com','app_password':'FAKE'}]);self.mailboxes.start()
    def tearDown(self):self.mailboxes.stop();self.p.tearDown()
    def row(self):return self.p.query('SELECT status FROM email_code_requests')[0][0]
    def code_messages(self):return [d for m,d in self.api.calls if m=='sendMessage' and d.get('chat_id')==7 and '<code>123456</code>' in d.get('text','')]
    def test_blue_button_and_icon_configuration(self):
        with self.s.db() as c:c.execute('INSERT OR REPLACE INTO category_icons VALUES (?,?)',('ui_email_code','987654321'))
        otp.show_button(self.s,self.api,7,self.oid)
        button=self.api.calls[-1][1]['reply_markup']['inline_keyboard'][0][0]
        self.assertEqual(button['style'],'primary')
        self.assertEqual(button['icon_custom_emoji_id'],'987654321')
        self.assertEqual(button['callback_data'],'emailcode:'+self.oid)
    def test_one_code_then_admin_escalation(self):
        self.bot.action(self.api,7,'emailcode:'+self.oid)
        with patch.object(otp,'fetch_code',return_value=('123456','mail:1:42')):
            otp.tick(self.s,self.api)
            otp.tick(self.s,self.api)
        self.assertEqual(len(self.code_messages()),1)
        self.assertEqual(self.row(),'sent')
        for _ in range(3):self.bot.action(self.api,7,'emailcode:'+self.oid)
        self.assertEqual(len(self.code_messages()),1)
        extra=[d for m,d in self.api.calls if m=='sendMessage' and d.get('chat_id')==self.admin and 'طلب كود إضافي' in d.get('text','')]
        self.assertEqual(len(extra),1)
    def test_concurrent_presses_create_one_request(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _:self.bot.action(self.api,7,'emailcode:'+self.oid),range(2)))
        self.assertEqual(self.p.query('SELECT count(*) FROM email_code_requests'),[(1,)])
    def test_wrong_customer_and_unpaid_order_rejected(self):
        self.bot.action(self.api,8,'emailcode:'+self.oid)
        unpaid=self.p.order('pc_2',status='paid')
        self.bot.action(self.api,7,'emailcode:'+unpaid)
        self.assertEqual(self.p.query('SELECT count(*) FROM email_code_requests'),[(0,)])
    def test_missing_mailbox_routes_to_admin(self):
        with patch.object(otp,'mailbox_configs',return_value=[]):
            self.bot.action(self.api,7,'emailcode:'+self.oid)
        self.assertEqual(self.row(),'escalated')
        self.assertIn('للإدارة',str(self.api.calls[-1]))
    def test_manual_admin_code_and_unauthorized_access(self):
        self.bot.action(self.api,7,'emailcodeadmin:'+self.oid)
        self.assertFalse(self.p.query("SELECT 1 FROM admin_state WHERE action='otp_manual'"))
        self.bot.action(self.api,self.admin,'emailcodeadmin:'+self.oid)
        self.bot.handle_receipt(self.api,{'chat':{'id':self.admin},'text':'123456'})
        self.assertEqual(self.row(),'sent')
        self.assertEqual(len(self.code_messages()),1)
        self.bot.action(self.api,7,'emailcode:'+self.oid)
        self.assertEqual(len(self.code_messages()),1)
    def test_timeout_and_api_failure(self):
        self.bot.action(self.api,7,'emailcode:'+self.oid)
        with self.s.db() as c:c.execute('UPDATE email_code_requests SET requested=?',(int(time.time())-200,))
        with patch.object(otp,'fetch_code',return_value=None):otp.tick(self.s,self.api)
        self.assertEqual(self.row(),'escalated')
    def test_mail_recipient_sender_freshness_and_ambiguity(self):
        now=time.time()
        def mail(target='user@example.com',sender='noreply@tm.openai.com',stamp=now,body='Your code is 123456',authenticated=True):
            m=email.message.EmailMessage()
            m['From']=sender;m['To']=target;m['Date']=formatdate(stamp,usegmt=True)
            m['Subject']='Your ChatGPT verification code'
            if authenticated:m['Authentication-Results']='mx.google.com; dkim=pass header.i=@tm.openai.com'
            m.set_content(body)
            return m.as_bytes()
        self.assertEqual(otp.parse_code(mail(),'user@example.com',now,now)[0],'123456')
        self.assertIsNone(otp.parse_code(mail(target='other@example.com'),'user@example.com',now,now))
        self.assertIsNone(otp.parse_code(mail(sender='attacker@example.com'),'user@example.com',now,now))
        self.assertIsNone(otp.parse_code(mail(stamp=now-1000),'user@example.com',now,now))
        self.assertIsNone(otp.parse_code(mail(authenticated=False),'user@example.com',now,now))
        self.assertIsNone(otp.parse_code(mail(body='Codes 123456 and 654321'),'user@example.com',now,now))
    def test_binding_does_not_guess_between_multiple_accounts(self):
        with self.s.db() as c:c.execute('DELETE FROM email_code_accounts')
        otp.bind_delivery(self.s,self.oid,'one@example.com two@example.com')
        self.assertFalse(self.p.query('SELECT * FROM email_code_accounts'))

    def test_imap_readonly_fetch_and_used_message_skip(self):
        now=time.time()
        m=email.message.EmailMessage()
        m['From']='otp@tm1.openai.com';m['To']='user@example.com'
        m['Date']=formatdate(now,usegmt=True);m['Subject']='Your login code'
        m['Authentication-Results']='mx.google.com; dkim=pass header.d=tm1.openai.com'
        m.set_content('123456')
        class Mail:
            def __enter__(inner):return inner
            def __exit__(inner,*args):pass
            def login(inner,user,password):self.assertEqual((user,password),('inbox@gmail.com','FAKE'))
            def select(inner,folder,readonly=False):self.assertTrue(readonly)
            def response(inner,name):return name,[b'5']
            def uid(inner,command,*args):
                return ('OK',[b'40 41']) if command=='search' else ('OK',[(b'meta',m.as_bytes())])
        with patch.object(otp.imaplib,'IMAP4_SSL',return_value=Mail()):
            self.assertEqual(otp.fetch_code('user@example.com',now),('123456','inbox@gmail.com:b\'5\':41'))
            self.assertIsNone(otp.fetch_code('user@example.com',now,{"inbox@gmail.com:b'5':40","inbox@gmail.com:b'5':41"}))


class GmailOAuth(unittest.TestCase):
    def test_single_mailbox_oauth_precedence_and_incomplete_config(self):
        config={'GMAIL_OTP_USER':'inbox@gmail.com','GMAIL_OTP_CLIENT_ID':'client',
                'GMAIL_OTP_CLIENT_SECRET':'secret','GMAIL_OTP_REFRESH_TOKEN':'refresh',
                'GMAIL_OTP_ACCOUNTS':'invalid legacy value'}
        with patch.dict(os.environ,config,clear=True):
            self.assertEqual(len(otp.mailbox_configs()),1)
            self.assertEqual(otp.mailbox_configs()[0]['refresh_token'],'refresh')
            del os.environ['GMAIL_OTP_REFRESH_TOKEN']
            with self.assertRaises(ValueError):otp.mailbox_configs()

    def test_oauth_readonly_recipient_matching_and_used_message_skip(self):
        now=time.time();m=email.message.EmailMessage()
        m['From']='otp@tm1.openai.com';m['To']='user@example.com'
        m['Delivered-To']='inbox@gmail.com'
        m['Date']=formatdate(now,usegmt=True);m['Subject']='Your login code'
        m['Authentication-Results']='mx.google.com; dkim=pass header.d=tm1.openai.com'
        m.set_content('123456')
        cfg={'user':'inbox@gmail.com','client_id':'client','client_secret':'secret','refresh_token':'refresh'}
        def api(url,token=None,form=None):
            if url.endswith('/token'):
                self.assertEqual(form['grant_type'],'refresh_token')
                return {'access_token':'token','scope':'https://www.googleapis.com/auth/gmail.readonly'}
            self.assertEqual(token,'token')
            if url.endswith('/profile'):return {'emailAddress':'inbox@gmail.com'}
            if '/messages?' in url:return {'messages':[{'id':'42'}]}
            self.assertTrue(url.endswith('/messages/42?format=raw'))
            return {'raw':base64.urlsafe_b64encode(m.as_bytes()).decode(),'internalDate':str(int(now*1000))}
        with patch.object(otp,'google_json',side_effect=api):
            self.assertEqual(otp.fetch_oauth_code(cfg,'user@example.com',now,()),('123456','inbox@gmail.com:gmail:42'))
            self.assertIsNone(otp.fetch_oauth_code(cfg,'another@example.com',now,()))
            self.assertIsNone(otp.fetch_oauth_code(cfg,'user@example.com',now,{'inbox@gmail.com:gmail:42'}))
        with patch.object(otp,'google_json',return_value={'access_token':'t','scope':'https://mail.google.com/'}):
            with self.assertRaises(ValueError):otp.fetch_oauth_code(cfg,'user@example.com',now,())
        with patch.object(otp,'google_json',side_effect=[{'access_token':'t'},{'emailAddress':'wrong@gmail.com'}]):
            with self.assertRaises(ValueError):otp.fetch_oauth_code(cfg,'user@example.com',now,())


if __name__=='__main__':unittest.main()
