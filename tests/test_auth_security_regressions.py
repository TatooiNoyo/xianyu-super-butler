"""禁用账号与外部消息 API 密钥的安全回归测试。"""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from app import reply_server
from app.db_manager import DBManager


class AuthSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'test.db')
        self.db = DBManager(self.path)
        self.addCleanup(lambda: self.db.close())
        patcher = mock.patch('app.db_manager.db_manager', self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_disabled_user_cannot_login_with_previously_issued_email_code(self):
        self.db.create_user('disabled', 'disabled@example.test', 'password123')
        user = self.db.get_user_by_username('disabled')
        self.db.save_verification_code(user['email'], '123456', 'login')
        self.db.set_user_active(user['id'], False)
        with mock.patch.object(reply_server, 'generate_token') as generate_token:
            result = asyncio.run(reply_server.login(reply_server.LoginRequest(
                email=user['email'], verification_code='123456',
            )))
        self.assertFalse(result.success)
        self.assertIn('禁用', result.message)
        generate_token.assert_not_called()

    def test_disabled_user_cannot_request_new_login_code(self):
        self.db.create_user('disabled', 'disabled@example.test', 'password123')
        self.db.set_user_active(self.db.get_user_by_username('disabled')['id'], False)
        self.db.save_captcha('captcha', 'ABCD')
        with (
            mock.patch.object(self.db, 'send_verification_email', new=mock.AsyncMock()) as send,
            mock.patch.object(reply_server, 'email_code_send_limiter', reply_server.email_code_send_limiter.__class__()),
        ):
            result = asyncio.run(reply_server.send_verification_code(reply_server.SendCodeRequest(
                email='disabled@example.test', type='login',
                captcha_session_id='captcha', captcha_code='ABCD',
            )))
        self.assertFalse(result.success)
        send.assert_not_awaited()

    def test_new_installations_have_distinct_persistent_keys(self):
        key = self.db.get_system_setting('qq_reply_secret_key')
        self.assertGreaterEqual(len(key), 32)
        other = DBManager(os.path.join(self.tmp.name, 'other.db'))
        try:
            self.assertNotEqual(key, other.get_system_setting('qq_reply_secret_key'))
        finally:
            other.close()
        self.db.close()
        self.db = DBManager(self.path)
        self.assertEqual(key, self.db.get_system_setting('qq_reply_secret_key'))

    def test_upgrade_rotates_public_keys_and_preserves_custom_key(self):
        for old_key in ('', 'xianyu_qq_reply_2024', 'xianyu_api_secret_2024', 'my-custom-key'):
            with self.subTest(old_key=old_key):
                self.db.set_system_setting('qq_reply_secret_key', old_key)
                self.db.close()
                self.db = DBManager(self.path)
                key = self.db.get_system_setting('qq_reply_secret_key')
                if old_key == 'my-custom-key':
                    self.assertEqual(key, old_key)
                else:
                    self.assertGreaterEqual(len(key), 32)
                    self.assertNotEqual(key, old_key)

    def test_api_key_validation_fails_closed(self):
        for stored_key in ('', None, 'xianyu_qq_reply_2024', 'xianyu_api_secret_2024'):
            with self.subTest(stored_key=stored_key), mock.patch.object(self.db, 'get_system_setting', return_value=stored_key):
                self.assertFalse(reply_server.verify_api_key(stored_key or ''))
        with mock.patch.object(self.db, 'get_system_setting', side_effect=RuntimeError('database unavailable')):
            self.assertFalse(reply_server.verify_api_key('xianyu_api_secret_2024'))
        self.assertTrue(reply_server.verify_api_key(self.db.get_system_setting('qq_reply_secret_key')))
        self.assertFalse(reply_server.verify_api_key('wrong-key'))

    def test_public_keys_cannot_reach_message_sender(self):
        with mock.patch('XianyuAutoAsync.XianyuLive.get_instance') as get_instance:
            for key in ('xianyu_qq_reply_2024', 'xianyu_api_secret_2024'):
                result = asyncio.run(reply_server.send_message_api(reply_server.SendMessageRequest(
                    api_key=key, cookie_id='account', chat_id='chat',
                    to_user_id='buyer', message='hello',
                )))
                self.assertFalse(result.success)
            get_instance.assert_not_called()
