"""人工验证 HTTP 和 WebSocket 通道必须校验登录态与账号归属。"""

import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import reply_server
from utils.captcha_remote_control import captcha_controller


class CaptchaAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.db = mock.Mock()
        self.db.get_cookie_owner_user.side_effect = lambda cid: {'mine': 2, 'other': 3}.get(cid)
        self.db.get_all_cookies.return_value = {'mine': 'cookie'}
        self.tokens = {'owner-token': {'user_id': 2, 'username': 'owner', 'timestamp': time.time()}}
        self.sessions = {
            cid: {'screenshot': f'{cid}-shot', 'captcha_info': {}, 'viewport': {}, 'completed': False}
            for cid in ('mine', 'other')
        }
        for patcher in (
            mock.patch('app.db_manager.db_manager', self.db),
            mock.patch.dict(reply_server.SESSION_TOKENS, self.tokens, clear=True),
            mock.patch.object(captcha_controller, 'active_sessions', self.sessions),
            mock.patch.object(captcha_controller, 'websocket_connections', {}),
            mock.patch.object(captcha_controller, 'update_screenshot', new=mock.AsyncMock(return_value='updated-shot')),
            mock.patch.object(captcha_controller, 'handle_mouse_event', new=mock.AsyncMock(return_value=True)),
            mock.patch.object(captcha_controller, 'check_completion', new=mock.AsyncMock(return_value=False)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(reply_server.app)
        self.addCleanup(self.client.close)
        self.headers = {'Authorization': 'Bearer owner-token'}

    def _requests(self, cid):
        return [
            ('GET', f'/api/captcha/session/{cid}', None),
            ('GET', f'/api/captcha/screenshot/{cid}', None),
            ('POST', '/api/captcha/mouse_event', {'session_id': cid, 'event_type': 'down', 'x': 1, 'y': 2}),
            ('POST', '/api/captcha/check_completion', {'session_id': cid}),
            ('GET', f'/api/captcha/status/{cid}', None),
            ('DELETE', f'/api/captcha/session/{cid}', None),
        ]

    def test_anonymous_http_requests_are_denied_before_reading_or_mutating(self):
        requests = [('GET', '/api/captcha/sessions', None)] + self._requests('mine')
        for method, path, body in requests:
            with self.subTest(path=path, method=method):
                response = self.client.request(method, path, json=body)
                self.assertEqual(response.status_code, 401)
        self.assertIn('mine', self.sessions)
        captcha_controller.update_screenshot.assert_not_awaited()
        captcha_controller.handle_mouse_event.assert_not_awaited()
        captcha_controller.check_completion.assert_not_awaited()

    def test_other_users_sessions_are_hidden_and_cannot_be_controlled(self):
        response = self.client.get('/api/captcha/sessions', headers=self.headers)
        self.assertEqual([s['session_id'] for s in response.json()['sessions']], ['mine'])
        for method, path, body in self._requests('other'):
            with self.subTest(path=path, method=method):
                self.assertEqual(self.client.request(method, path, json=body, headers=self.headers).status_code, 404)
        self.assertIn('other', self.sessions)
        captcha_controller.handle_mouse_event.assert_not_awaited()

    def test_owner_can_read_operate_and_close_session(self):
        detail = self.client.get('/api/captcha/session/mine', headers=self.headers)
        self.assertEqual(detail.json()['screenshot'], 'mine-shot')
        for method, path, body in self._requests('mine'):
            with self.subTest(path=path, method=method):
                self.assertEqual(self.client.request(method, path, json=body, headers=self.headers).status_code, 200)
        captcha_controller.handle_mouse_event.assert_awaited_once_with('mine', 'down', 1, 2)
        self.assertNotIn('mine', self.sessions)

    def test_anonymous_foreign_and_expired_websockets_are_denied(self):
        reply_server.SESSION_TOKENS['expired-token'] = {'user_id': 2, 'username': 'owner', 'timestamp': 0}
        for cid, protocols in (
            ('mine', None),
            ('other', ['captcha', 'bearer.owner-token']),
            ('mine', ['captcha', 'bearer.expired-token']),
        ):
            with self.subTest(cid=cid, protocols=protocols):
                with self.assertRaises(WebSocketDisconnect) as exc:
                    with self.client.websocket_connect(f'/api/captcha/ws/{cid}', subprotocols=protocols):
                        self.fail('unauthorized websocket accepted')
                self.assertEqual(exc.exception.code, 1008)
        self.assertEqual(captcha_controller.websocket_connections, {})

    def test_browser_websocket_authentication_and_revocation(self):
        with self.client.websocket_connect('/api/captcha/ws/mine', subprotocols=['captcha', 'bearer.owner-token']) as ws:
            self.assertEqual(ws.accepted_subprotocol, 'captcha')
            self.assertEqual(ws.receive_json()['screenshot'], 'mine-shot')
            ws.send_json({'type': 'ping'})
            self.assertEqual(ws.receive_json(), {'type': 'pong'})
            reply_server.SESSION_TOKENS.pop('owner-token')
            ws.send_json({'type': 'mouse_event', 'event_type': 'down', 'x': 1, 'y': 2})
            with self.assertRaises(WebSocketDisconnect) as exc:
                ws.receive_json()
            self.assertEqual(exc.exception.code, 1008)
        captcha_controller.handle_mouse_event.assert_not_awaited()

    def test_owner_can_connect_before_browser_session_is_ready(self):
        self.sessions.pop('mine')

        async def create_session(_):
            self.sessions['mine'] = {'screenshot': 'ready', 'captcha_info': {}, 'viewport': {}}

        with mock.patch('app.api_captcha_remote.asyncio.sleep', side_effect=create_session):
            with self.client.websocket_connect('/api/captcha/ws/mine', subprotocols=['captcha', 'bearer.owner-token']) as ws:
                self.assertEqual(ws.receive_json()['screenshot'], 'ready')
