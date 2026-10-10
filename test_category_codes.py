import unittest
from unittest.mock import patch
import test_payment_execution as fixtures
import category_codes as codes

class CodePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixtures.Payments.setUpClass()
    @classmethod
    def tearDownClass(cls): fixtures.Payments.tearDownClass()
    def setUp(self):
        self.p = fixtures.Payments('test_unauthorized_callback')
        self.p.setUp()
        self.s, self.api, self.bot, self.admin = self.p.s, self.p.api, self.p.bot, self.p.admin
    def tearDown(self): self.p.tearDown()
    def test_english_catalog_repairs_arabic_cached_as_english(self):
        import re
        import catalog_language
        source='<tg-emoji emoji-id="123456">⬇️</tg-emoji> جميع مايخص ChatGPT\nجميع المنتجات بضمان\nجميع المنتجات لمدة شهر\nماعدا المنتج الثالث (ع ايميلك) المدة ٣ شهور'
        with self.s.db() as c:
            c.execute("INSERT OR REPLACE INTO preferences(cid,lang,currency) VALUES (7,'en','USD')")
            for lang in ('ar','en'):
                c.execute('INSERT OR REPLACE INTO product_text VALUES (?,?,?,?)',('chatgpt','category_description',lang,source))
            for pid,title in [('pc_0','بيانات جاهزة'),('pc_1','على ايميلك'),('pc_2','مشترك،مع عدد قليل'),('pc_3','مشترك')]:
                c.execute("UPDATE admin_products SET name=?,category_id='chatgpt' WHERE pid=?",(title,pid))
                c.execute('INSERT OR REPLACE INTO product_text VALUES (?,?,?,?)',(pid,'name','en',title))
        with patch('chatgpt_extension.translate_text',side_effect=AssertionError('Offline translation must not need credentials')):
            self.bot.action(self.api,7,'product:chatgpt')
        data=next(d for m,d in self.api.calls if m=='sendMessage' and 'Everything related to' in d.get('text',''))
        self.assertTrue(catalog_language.english(data['text']))
        self.assertIn('3 months',data['text'])
        self.assertIn('<tg-emoji emoji-id="123456">⬇️</tg-emoji>',data['text'])
        labels=str(data['reply_markup'])
        self.assertIn('Ready account',labels)
        self.assertIn('On your email',labels)
        self.assertIn('Shared with a small group',labels)
        self.assertTrue(catalog_language.english(labels))
        with self.s.db() as c:
            self.assertEqual(c.execute("SELECT value FROM product_text WHERE pid='chatgpt' AND field='category_description' AND lang='ar'").fetchone()[0],source)
    def test_failed_translation_never_caches_arabic_as_english(self):
        import catalog_language
        source='تعليمات مختلفة لا توجد في القاموس'
        with patch('chatgpt_extension.translate_text',return_value=source):
            self.assertEqual(self.s.auto_translate(source,'en'),'')
            with self.s.db() as c:
                c.execute('INSERT OR REPLACE INTO product_text VALUES (?,?,?,?)',('chatgpt','category_description','ar',source))
            self.assertTrue(catalog_language.english(self.s.category_description_html('chatgpt',7,'en')))
        with self.s.db() as c:
            self.assertIsNone(c.execute("SELECT 1 FROM product_text WHERE pid='chatgpt' AND field='category_description' AND lang='en'").fetchone())
    def test_actual_admin_dashboard_links_to_edit_panel(self):
        self.bot.action(self.api,self.admin,'admin')
        rows=[row for method,data in self.api.calls if method=='sendMessage' for row in data.get('reply_markup',{}).get('inline_keyboard',[])]
        button=next(b for row in rows for b in row if b.get('callback_data')=='codeui:panel')
        self.assertEqual(button['text'],'إعداد صفحة أكواد ChatGPT')
        self.bot.action(self.api,self.admin,button['callback_data'])
        self.assertIn('codeui:text_ar',str(self.api.calls[-1][1]['reply_markup']))
    def test_button_under_products_before_back_and_other_categories_unchanged(self):
        with self.s.db() as c:
            c.execute('INSERT OR REPLACE INTO category_icons VALUES (?,?)', ('ui_category_codes','987654321'))
        rows = [[self.s.btn('product','item:pc_1')], self.s.nav(7)]
        markup = self.s.product_keyboard(7, rows, 'c:chatgpt')['inline_keyboard']
        self.assertEqual(markup[1][0]['callback_data'],'codepage:chatgpt')
        self.assertNotIn('style',markup[1][0])
        self.assertEqual(markup[1][0]['icon_custom_emoji_id'],'987654321')
        self.assertEqual(markup[2][0]['callback_data'],'products')
        self.assertEqual(codes.add_rows(self.s,7,rows,'c:youtube'),rows)
    def test_edit_emoji_entities_preview_persistence_and_unauthorized(self):
        self.bot.action(self.api,8,'codeui:text_ar')
        self.assertEqual(self.p.query("SELECT * FROM admin_state WHERE cid=8"),[])
        self.bot.action(self.api,self.admin,'codeui:text_ar')
        text='🔐 شرح طلب الكود'
        entities=[{'type':'custom_emoji','offset':0,'length':2,'custom_emoji_id':'123456'}]
        self.assertTrue(self.bot.handle_receipt(self.api,{'chat':{'id':self.admin},'text':text,'entities':entities}))
        self.bot.action(self.api,7,'codepage:chatgpt')
        data=self.api.calls[-1][1]
        self.assertEqual(data['text'],text)
        self.assertEqual(data['entities'],entities)
        self.assertNotIn('parse_mode',data)
        self.assertIn('https://t.me/SOQ_IDbot',str(data))
        self.assertEqual(codes.get(self.s,'text_ar'),text)
    def test_only_customer_eligible_orders_including_products_named_plus(self):
        with self.s.db() as c:
            c.execute("UPDATE admin_products SET category_id='chatgpt' WHERE pid='pc_2'")
        own=self.p.order('pc_2',status='delivered')
        other=self.p.order('pc_2',status='delivered')
        unpaid=self.p.order('pc_2',status='paid')
        with self.s.db() as c: c.execute('UPDATE orders SET cid=8 WHERE id=?',(other,))
        self.bot.action(self.api,self.admin,'emailcodetoggle:pc_2')
        self.bot.action(self.api,7,'codepage:chatgpt')
        buttons=str(self.api.calls[-1][1]['reply_markup'])
        self.assertIn('emailcode:'+own,buttons)
        self.assertNotIn(other,buttons)
        self.assertNotIn(unpaid,buttons)

if __name__=='__main__': unittest.main()
