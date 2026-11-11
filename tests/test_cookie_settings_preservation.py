"""刷新 Cookie 时保留账号资料、互动开关及关联业务数据。"""

import os
import tempfile
import unittest

from app.db_manager import DBManager


class CookieSettingsPreservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = DBManager(os.path.join(self.tmp.name, 'cookies.db'))
        self.addCleanup(self.db.close)

    def test_cookie_refresh_preserves_all_other_account_columns(self):
        self.db.save_cookie('account', 'unb=account; cookie2=old', 1)
        self.db.conn.execute("""UPDATE cookies SET
            auto_confirm=0, remark='备注', pause_duration=99,
            username='seller', password='saved-password', show_browser=1,
            nickname='昵称', avatar_url='https://example.test/avatar', location='杭州',
            bio='简介', followers=12, following=34, profile_updated_at='2026-01-01',
            created_at='2025-01-01' WHERE id='account'""")
        self.db.conn.commit()
        self.db.update_buyer_interaction_settings(
            'account', auto_rate_enabled=True, auto_flower_enabled=True,
            auto_thanks_enabled=True, auto_receive_flower_enabled=True,
        )
        self.db.conn.row_factory = None
        columns = [r[1] for r in self.db.conn.execute('PRAGMA table_info(cookies)')]
        before = dict(zip(columns, self.db.conn.execute("SELECT * FROM cookies WHERE id='account'").fetchone()))
        for user_id in (None, 1):
            with self.subTest(user_id=user_id):
                self.assertTrue(self.db.save_cookie('account', 'unb=account; cookie2=new', user_id))
                after = dict(zip(columns, self.db.conn.execute("SELECT * FROM cookies WHERE id='account'").fetchone()))
                self.assertEqual(after.pop('value'), 'unb=account; cookie2=new')
                self.assertEqual(after, {k: v for k, v in before.items() if k != 'value'})

    def test_update_preserves_child_records_with_foreign_keys_enabled(self):
        self.db.conn.execute('PRAGMA foreign_keys=ON')
        self.db.save_cookie('account', 'old-cookie', 1)
        self.db.conn.execute("INSERT INTO keywords(cookie_id,keyword,reply) VALUES ('account','hello','reply')")
        self.db.conn.commit()
        self.assertTrue(self.db.save_cookie('account', 'new-cookie'))
        self.assertEqual(self.db.conn.execute('SELECT keyword,reply FROM keywords').fetchall(), [('hello', 'reply')])

    def test_new_account_uses_defaults_and_requested_owner(self):
        self.assertTrue(self.db.save_cookie('new', 'new-cookie', 1))
        self.assertEqual(self.db.get_cookie_owner_user('new'), 1)
        self.assertFalse(any(self.db.get_buyer_interaction_settings('new').values()))
        self.assertEqual(self.db.conn.execute("SELECT auto_confirm,pause_duration FROM cookies WHERE id='new'").fetchone(), (1, 10))
