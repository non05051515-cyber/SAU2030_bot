from test_button_names import ButtonNamesTests


class AddExistingCategoryTests(ButtonNamesTests):
    def add_product(self, category_id):
        self.bot.action(self.api, self.admin, 'addtocategory:' + category_id)
        for value in ('Second product', 'وصف المنتج', '4', '3', 'نعم'):
            self.assertTrue(self.bot.handle_admin_product(self.api, {'chat': {'id': self.admin}, 'text': value}))
        with self.s.db() as db:
            pid = db.execute('SELECT pid FROM admin_products WHERE category_id=? AND name=?',
                             (category_id, 'Second product')).fetchone()[0]
            self.assertEqual(db.execute('SELECT status FROM channel_publish_choices WHERE pid=?', (pid,)).fetchone()[0], 'pending')
        self.assertIn('لاحقًا', str(self.api.calls[-1]))
        self.bot.action(self.api, 75, 'product:' + category_id)
        self.assertIn('Second product', str(self.api.calls[-1]))
        return pid

    def test_custom_category_keeps_first_product(self):
        with self.s.db() as db:
            db.execute("INSERT INTO admin_categories VALUES ('existing','Existing','now')")
            db.execute("INSERT INTO admin_products(pid,name,price_sar,created_at,category_id,price_usd,stock) VALUES ('first','First product','15','now','existing','4',2)")
        self.add_product('existing')
        self.assertIn('First product', str(self.api.calls[-1]))
        with self.s.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM admin_categories').fetchone()[0], 1)

    def test_builtin_category_includes_new_product(self):
        self.bot.action(self.api, self.admin, 'admin')
        self.assertIn('admin:addtocategory', [b['callback_data'] for b in self.api.buttons()])
        self.add_product('youtube')
        self.assertIn('options:youtube', str(self.api.calls[-1]))
        with self.s.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM admin_categories').fetchone()[0], 0)

    def test_customer_cannot_start_add_flow(self):
        self.bot.action(self.api, 75, 'addtocategory:youtube')
        with self.s.db() as db:
            self.assertIsNone(db.execute('SELECT * FROM admin_state WHERE cid=75').fetchone())
