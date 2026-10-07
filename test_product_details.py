import unittest
import test_product_options as options


class DetailsTests(unittest.TestCase):
    setUp = options.OptionsTests.setUp
    tearDown = options.OptionsTests.tearDown
    def test_admin_fields_escape_icons_and_persist(self):
        admin=self.s.G['ADMIN_ID']
        self.b.action(self.api,123,'detailadd:'+self.pid)
        with self.s.db() as c:
            self.assertIsNone(c.execute('SELECT action FROM admin_state WHERE cid=123').fetchone())
        self.b.action(self.api,admin,'detailadd:'+self.pid)
        self.b.handle_receipt(self.api,{'chat':{'id':admin},'text':'المدة\nشهر <خاص>'})
        with self.s.db() as c:
            fid=c.execute('SELECT id FROM product_detail_fields').fetchone()[0]
        self.b.action(self.api,admin,'detailicon:extra_'+fid+':'+self.pid)
        self.s.handle_info_icon(self.api,{'chat':{'id':admin},'text':'⭐','entities':[{'type':'custom_emoji','custom_emoji_id':'123456','offset':0,'length':1}]})
        self.b.action(self.api,admin,'infowarranty:'+self.pid)
        self.s.handle_info_warranty(self.api,{'chat':{'id':admin},'text':'29 يوم'})
        rendered=self.s.info_block(self.pid,123)
        self.assertIn('شهر &lt;خاص&gt;',rendered)
        self.assertIn('emoji-id="123456"',rendered)
        self.assertIn('29 يوم',rendered)
        self.assertTrue(rendered.splitlines()[-1].endswith('24'))
        self.b.action(self.api,admin,'detailtoggle:'+fid)
        self.assertNotIn('شهر',self.s.info_block(self.pid,123))
        self.b.action(self.api,admin,'detaildelete:'+fid)
        with self.s.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM product_detail_fields').fetchone()[0],0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM product_info_icons WHERE field=?",('extra_'+fid,)).fetchone()[0],0)


    def test_same_supplier_identity_shows_saved_field_and_icon(self):
        import product_details
        product_details.prepare(self.s)
        with self.s.db() as c:
            for pid in (self.pid, 'same_product'):
                c.execute("INSERT OR REPLACE INTO supplier_api(pid,provider,service_id,variant_id,enabled) VALUES (?,?,?,?,?)",(pid,'pandora','exact_service','variant1',1))
            c.execute("INSERT INTO product_detail_fields VALUES (?,?,?,?,?)",('saved_field','same_product','عدد الشراء','301',1))
            c.execute("INSERT INTO product_info_icons(pid,field,custom_emoji_id,fallback_emoji) VALUES (?,?,?,?)",('same_product','extra_saved_field','777','⭐'))
        rendered=self.s.info_block(self.pid,123)
        self.assertIn('301',rendered)
        self.assertIn('emoji-id="777"',rendered)
        with self.s.db() as c:
            c.execute("UPDATE supplier_api SET variant_id='different' WHERE pid='same_product'")
        self.assertNotIn('301',self.s.info_block(self.pid,123))

    def test_clean_description_retains_prose(self):
        import product_details
        raw='✂️ شهر كامل\n💵 السعر: $1.39\n📦 المخزون: 43\n💦 الوصف:\n• حساب خاص <b>30 يومًا</b>'
        cleaned=product_details.clean_description(raw)
        self.assertNotIn('$1.39',cleaned)
        self.assertNotIn('المخزون',cleaned)
        self.assertIn('<b>30 يومًا</b>',cleaned)


if __name__=='__main__':unittest.main()
