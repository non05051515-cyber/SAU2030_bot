import json
from test_button_names import ButtonNamesTests


class DescriptionEmojiTests(ButtonNamesTests):
    def save_description(self, text, entities):
        self.bot.action(self.api, self.admin, 'txtedit:description:ar:' + self.pid)
        self.assertTrue(self.bot.handle_receipt(self.api, {
            'chat': {'id': self.admin}, 'text': text, 'entities': entities,
        }))

    def test_utf16_offsets_and_html_escaping(self):
        raw = '  😀 وصف 📱 & <b>خاص</b> ✅  '
        entities = []
        for emoji, emoji_id in [('📱', '123456'), ('✅', '987654')]:
            entities.append({'type': 'custom_emoji', 'custom_emoji_id': emoji_id,
                             'offset': len(raw[:raw.index(emoji)].encode('utf-16-le')) // 2,
                             'length': len(emoji.encode('utf-16-le')) // 2})
        self.save_description(raw, entities)
        self.bot.action(self.api, 75, 'item:' + self.pid)
        text = self.api.calls[-1][1]['text']
        self.assertIn('<tg-emoji emoji-id="123456">📱</tg-emoji>', text)
        self.assertIn('<tg-emoji emoji-id="987654">✅</tg-emoji>', text)
        self.assertIn('&amp; &lt;b&gt;خاص&lt;/b&gt;', text)
        self.assertEqual(self.s.product_description(self.pid, 75), raw.strip())
        self.bot.action(self.api, 75, 'deliverynote:' + self.pid)
        self.assertIn('<tg-emoji emoji-id="123456">', self.api.calls[-1][1]['text'])

    def test_plain_edit_clears_emoji_and_language_isolated(self):
        self.save_description('📱 وصف', [{'type': 'custom_emoji', 'offset': 0, 'length': 2,
                                          'custom_emoji_id': '123456'}])
        self.save_description('📱 وصف', [])
        self.assertNotIn('tg-emoji', self.s.product_description_html(self.pid, 75))
        self.assertNotIn('tg-emoji', self.s.product_description_html('pd_04', 75))

    def test_new_product_draft_keeps_custom_emoji(self):
        with self.s.db() as db:
            db.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)',
                       (self.admin, 'add_product_desc', json.dumps({'current': {'name': 'test'}})))
        self.s.handle_admin_product(self.api, {'chat': {'id': self.admin}, 'text': '📱 وصف',
            'entities': [{'type': 'custom_emoji', 'offset': 0, 'length': 2, 'custom_emoji_id': '123456'}]})
        with self.s.db() as db:
            payload = json.loads(db.execute('SELECT value FROM admin_state WHERE cid=?', (self.admin,)).fetchone()[0])
        self.assertIn('<tg-emoji emoji-id="123456">', payload['current']['description_html'])
