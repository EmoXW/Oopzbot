import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from starlette.requests import Request  # noqa: E402

from plugins.delta_force_v2 import DeltaForceV2Plugin  # noqa: E402
from web import delta_login  # noqa: E402


class TicketTests(unittest.TestCase):
    def setUp(self):
        delta_login._tickets.clear()

    def request_status(self, ticket):
        async def receive():
            return {"type": "http.request", "body": ticket.encode(), "more_body": False}
        response = asyncio.run(delta_login.login_status(Request({"type": "http", "method": "POST", "path": "/delta-login/status", "headers": []}, receive)))
        return response, json.loads(response.body)

    def test_expiry_and_no_secrets_or_cache(self):
        ticket = delta_login.create_ticket("qr-data", 30)
        result, data = self.request_status(ticket)
        self.assertEqual(data['image'], 'qr-data')
        self.assertIn('no-store', result.headers['cache-control'])
        with patch('web.delta_login.time.monotonic', return_value=10**15):
            self.assertEqual(self.request_status(ticket)[1]['status'], 'expired')
        self.assertNotIn(ticket, asyncio.run(delta_login.login_page()).body.decode())
        self.assertEqual(self.request_status('x' * 513)[0].status_code, 400)

    def test_terminal_states_clear_qr_and_are_isolated(self):
        first = delta_login.create_ticket('one', 30)
        second = delta_login.create_ticket('two', 30)
        for status in ('success', 'cancelled', 'error'):
            delta_login.finish_ticket(first, status, 'done')
            result = self.request_status(first)[1]
            self.assertEqual(result['status'], status)
            self.assertEqual(result['image'], '')
        self.assertEqual(delta_login.read_ticket(second)['image'], 'two')


class LoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_only_after_binding_and_save(self):
        plugin: Any = DeltaForceV2Plugin()
        plugin.config = {'client_id': 'client', 'login_page_base_url': ''}
        plugin.api = SimpleNamespace(request=AsyncMock(side_effect=[
            {'frameworkToken': 'secret', 'qr_image': 'iVBORw0KGgo='},
            {'status': 'success', 'frameworkToken': 'secret'}, {},
        ]))
        plugin.store = SimpleNamespace(put=AsyncMock())
        sender = SimpleNamespace(send_private_message=AsyncMock(return_value={'status': True}))
        with patch('plugins.delta_force_v2.asyncio.sleep', new_callable=AsyncMock), patch.dict(
            'web.web_player_config.WEB_PLAYER_CONFIG', {'url': 'https://music.example.com'}
        ):
            await plugin._login('user', 'qq', SimpleNamespace(sender=sender))
        message = sender.send_private_message.call_args_list[0].args[1]
        ticket = message.split('#', 1)[1].split()[0]
        self.assertNotIn('secret', message)
        self.assertIn('https://music.example.com/delta-login#', message)
        plugin.store.put.assert_awaited_once_with('client', 'user', 'secret')
        self.assertEqual(delta_login.read_ticket(ticket)['status'], 'success')
        self.assertEqual(delta_login.read_ticket(ticket)['image'], '')

    async def test_cancel_removes_qr(self):
        plugin: Any = DeltaForceV2Plugin()
        plugin.config = {'client_id': 'c', 'login_page_base_url': 'http://localhost:10000'}
        plugin.api = SimpleNamespace(request=AsyncMock(return_value={'frameworkToken': 'secret', 'qr_image': 'iVBORw0KGgo='}))
        sent = asyncio.Event()
        async def send(*args):
            sent.set()
            return {'status': True}
        sender = SimpleNamespace(send_private_message=AsyncMock(side_effect=send))
        task = asyncio.create_task(plugin._login('u', 'qq', SimpleNamespace(sender=sender)))
        await sent.wait()
        ticket = sender.send_private_message.call_args.args[1].split('#', 1)[1].split()[0]
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(delta_login.read_ticket(ticket)['status'], 'cancelled')
        self.assertEqual(delta_login.read_ticket(ticket)['image'], '')
