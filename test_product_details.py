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


if __name__=='__main__':unittest.main()
