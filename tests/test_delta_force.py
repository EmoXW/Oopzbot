import asyncio
import base64
import hashlib
import hmac
import io
import json
import sys
import tempfile
import unittest
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'plugins'), str(ROOT / 'src'), str(ROOT)]

from domain.plugins.base import PluginConfig  # noqa: E402
from plugins import delta_force as module  # noqa: E402
from plugins.delta_force.api import APIError, Client, sign_headers  # noqa: E402
from plugins.delta_force.daily import TZ, parse_passwords  # noqa: E402
from plugins.delta_force.web import PREFIX  # noqa: E402

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jOt8AAAAASUVORK5CYII=')


class DeltaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.requests = []
        self.states = {}
        self.owners = {}
        self.next_state = 'pending'
        self.daily_count = 0
        self.role_data = {'name': '测试角色', 'level': 42}
        self.records_data = []
        today = datetime.now(TZ)
        self.daily_data = {'status': 'success', 'data': {
            'last_updated': today.isoformat(), 'update_date': today.strftime('%m月%d日每日密码已更新'),
            'passwords': [{'map_name': '零号大坝', 'password': '0012'},
                          {'map_name': '长弓溪谷', 'password': '3456'}],
        }}
        api = web.Application()
        api.router.add_route('*', '/{tail:.*}', self.api)
        self.server = TestServer(api)
        await self.server.start_server()
        self.addAsyncCleanup(self.server.close)
        self.app = FastAPI()
        self.plugin = module.DeltaForce()
        self.sender = SimpleNamespace(send_message=AsyncMock(return_value={'status': True}),
                                      send_private_message=AsyncMock(return_value={'status': True}))
        self.host = SimpleNamespace(sender=self.sender)
        self.config = PluginConfig('delta_force', {
            'api_base_url': str(self.server.make_url('/')).rstrip('/'),
            'api_key': 'test-key', 'api_secret': 'test-secret',
            'daily_password_url': str(self.server.make_url('/daily')),
        }, '', False)
        self.patches = [patch.object(module, 'DATA_DIR', self.tmp.name),
                        patch('web.web_player.app', self.app),
                        patch.dict('config.WEB_PLAYER_CONFIG', {'url': 'https://music.example.test'})]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        await self.plugin.on_load(self.host, self.config)
        self.addAsyncCleanup(self.plugin.on_unload)
        self.browser = TestClient(self.app)

    async def api(self, request):
        if request.path == '/daily':
            self.assertNotIn('X-API-Key', request.headers)
            self.assertNotIn('X-User-ID', request.headers)
            self.assertNotIn('Cookie', request.headers)
            self.daily_count += 1
            return web.json_response(self.daily_data)
        body = await request.read()
        user = request.headers['X-User-ID']
        canonical = '\n'.join((request.method, request.raw_path, request.headers['X-Timestamp'],
                               request.headers['X-Nonce'], user, hashlib.sha256(body).hexdigest()))
        expected = hmac.new(b'test-secret', canonical.encode(), hashlib.sha256).hexdigest()
        if request.headers.get('X-API-Key') != 'test-key' or not hmac.compare_digest(expected, request.headers['X-Signature']):
            return web.json_response({'error': {'code': 'BAD_SIGNATURE'}}, status=401)
        self.requests.append((request.method, request.path, user, dict(request.query)))
        if request.method == 'POST':
            sid = 'session-' + str(len(self.owners) + 1)
            self.owners[sid] = user
            self.states[sid] = self.next_state
            provider = json.loads(body)['provider']
            return web.json_response({'session_id': sid, 'provider': provider, 'expires_in': 300,
                                      'poll_after_seconds': 2, 'qr_url': f'/v1/login/sessions/{sid}/qr'})
        sid = request.path.split('/')[4] if request.path.startswith('/v1/login/') else request.path.split('/')[3]
        if self.owners.get(sid) != user:
            return web.json_response({'error': {'code': 'SESSION_NOT_FOUND'}}, status=404)
        if request.method == 'DELETE':
            self.owners.pop(sid, None)
            return web.json_response({'deleted': True})
        if request.path.endswith('/qr'):
            return web.Response(body=PNG, content_type='image/png')
        if request.path.endswith('/role'):
            return web.json_response({'result': {'error_code': 0}, 'role_info': self.role_data})
        if request.path.endswith('/report'):
            return web.json_response({'result': {'error_code': 0}, 'season': {'kills': 12}})
        if request.path.endswith('/records'):
            return web.json_response({'result': {'error_code': 0}, 'sols': self.records_data, 'tdms': self.records_data})
        return web.json_response({'state': self.states[sid], 'session_id': sid, 'provider': 'qq'})

    async def command(self, text, user='user-a'):
        return await self.plugin.handle_mention(text, 'channel', 'area', user, self.host)

    async def test_admin_queries_target_owner_and_real_game_nickname(self):
        self.owners['bound'] = 'user-b'
        await self.plugin.save_binding('user-b', 'bound', 'qq')
        self.records_data = [{'playerId': '6454372124981955472', 'name': '',
                              'gameResult': 1, 'gainedPrice': 183435, 'ProfitLoss': -551904,
                              'gameTime': 192, 'killPlayer': 1}]
        with patch('config.ADMIN_UIDS', ['user-a']):
            await self.command('三角洲 (met)user-b(met) 信息')
            self.assertIn('角色名：测试角色', self.sender.send_message.await_args.args[0])
            await self.command('三角洲 (met)user-b(met)战绩')
        text = self.sender.send_message.await_args.args[0]
        self.assertIn('角色名：测试角色', text)
        self.assertIn('撤离失败', text)
        self.assertIn('3分12秒', text)
        self.assertIn('-551,904', text)
        self.assertNotIn('playerId', text)
        self.assertNotIn('6454372124981955472', text)
        self.assertTrue(all(request[2] == 'user-b' for request in self.requests))
        self.assertEqual(sum(request[1].endswith('/role') for request in self.requests), 2)

    async def test_proxy_permission_fallback_override_and_read_only(self):
        with patch('config.ADMIN_UIDS', ['user-a']):
            self.assertTrue(self.plugin.is_query_admin('user-a'))
            self.plugin.settings['admin_uids'] = ['custom']
            self.assertFalse(self.plugin.is_query_admin('user-a'))
            self.assertTrue(self.plugin.is_query_admin('custom'))
            await self.command('三角洲 (met)user-b(met) 信息')
            self.assertIn('只有管理员', self.sender.send_message.await_args.args[0])
            self.plugin.settings['admin_uids'] = []
            for action in ('解绑', '登录', '状态', '(met)user-c(met) 信息'):
                await self.command('三角洲 (met)user-b(met) ' + action)
            self.assertEqual(self.requests, [])

    async def test_default_qq_login_page_and_success_notification(self):
        self.next_state = 'authenticated'
        self.assertTrue(await self.command('三角洲 登录'))
        token, page = next(iter(self.plugin.pages.items()))
        private = self.sender.send_private_message.await_args.args[1]
        self.assertIn('https://music.example.test' + PREFIX + '/' + token, private)
        self.assertEqual(page['provider'], 'qq')
        response = self.browser.get(PREFIX + '/' + token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store, max-age=0')
        self.assertNotIn('test-secret', response.text)
        self.assertNotIn(page['sid'], response.text)
        self.assertEqual(self.browser.get(PREFIX + '/' + token + '/qr').content, PNG)
        await asyncio.wait_for(self.plugin.tasks['user-a'], 5)
        status = self.browser.get(PREFIX + '/' + token + '/status').json()
        self.assertEqual(status['state'], 'authenticated')
        self.assertTrue(status['terminal'])
        self.assertEqual(await self.plugin.binding('user-a'), (page['sid'], 'qq'))
        self.assertIn('登录成功', self.sender.send_private_message.await_args.args[1])
        self.assertEqual(self.browser.get(PREFIX + '/' + token + '/qr').status_code, 410)
        await self.command('三角洲 信息')
        self.assertIn('测试角色', self.sender.send_message.await_args.args[0])

    async def test_wechat_failure_notifies_without_binding(self):
        self.next_state = 'verification_required'
        await self.command('三角洲 登录 wx', 'wx-user')
        page = next(iter(self.plugin.pages.values()))
        self.assertEqual(page['provider'], 'wechat')
        await asyncio.wait_for(self.plugin.tasks['wx-user'], 5)
        self.assertIsNone(await self.plugin.binding('wx-user'))
        self.assertIn('额外验证', self.sender.send_private_message.await_args.args[1])
        self.assertTrue(page['terminal'])

    async def test_expiry_unknown_token_and_unload_cleanup(self):
        await self.command('三角洲 登录')
        token, item = next(iter(self.plugin.pages.items()))
        item['deadline'] = 0
        await asyncio.wait_for(self.plugin.tasks['user-a'], 5)
        self.assertEqual(item['state'], 'expired')
        self.assertIn('失效', self.sender.send_private_message.await_args.args[1])
        item['expires'] = 0
        self.assertEqual(self.browser.get(PREFIX + '/' + token).status_code, 410)
        self.assertEqual(self.browser.get(PREFIX + '/unknown/status').status_code, 410)
        await self.plugin.on_unload()
        self.assertEqual(len(self.app.routes), 4)
        self.assertFalse(self.plugin.tasks)

    async def test_user_isolation_and_parameterized_queries(self):
        self.owners['bound'] = 'user-a'
        self.states['bound'] = 'authenticated'
        await self.plugin.save_binding('user-a', 'bound', 'qq')
        await self.command('三角洲 信息', 'user-b')
        self.assertIn('请先发送', self.sender.send_message.await_args.args[0])
        with self.assertRaises(APIError):
            assert self.plugin.client is not None
            await self.plugin.client.request('GET', '/v1/sessions/bound/role', 'user-b')
        await self.command('三角洲 统计 12 全面')
        self.assertEqual(self.requests[-1][3], {'area': '36', 'season': '12', 'queue': 'tdm'})
        await self.command('三角洲 战绩 烽火 8')
        self.assertEqual(self.requests[-1][3]['size'], '8')
        self.assertIn('暂无战绩', self.sender.send_message.await_args.args[0])
        await self.command('三角洲 解绑')
        self.assertIsNone(await self.plugin.binding('user-a'))

    async def test_persisted_binding_and_reload_route_cleanup(self):
        await self.plugin.save_binding('u', 'sid', 'wechat')
        await self.plugin.on_config_reload(self.host, self.config)
        self.assertEqual(await self.plugin.binding('u'), ('sid', 'wechat'))
        self.assertEqual(len(self.app.routes), 7)

    async def test_daily_password_does_not_require_login_or_game_keys_and_caches(self):
        assert self.plugin.client is not None
        self.plugin.client.key = self.plugin.client.secret = ''
        await self.command('三角洲 每日密码')
        text = self.sender.send_message.await_args.args[0]
        self.assertIn('零号大坝：0012', text)
        self.assertIn('长弓溪谷：3456', text)
        self.assertIsNone(await self.plugin.binding('user-a'))
        await self.command('三角洲 密码 大坝')
        text = self.sender.send_message.await_args.args[0]
        self.assertIn('零号大坝：0012', text)
        self.assertNotIn('长弓溪谷', text)
        self.assertEqual(self.daily_count, 1)

    async def test_stale_daily_passwords_are_rejected_without_repeated_requests(self):
        self.daily_data['data']['last_updated'] = (datetime.now(TZ) - timedelta(days=1)).isoformat()
        await self.command('三角洲 每日密码')
        self.assertIn('尚未更新到今天', self.sender.send_message.await_args.args[0])
        await self.command('三角洲 每日密码')
        self.assertEqual(self.daily_count, 1)

    async def test_role_reply_is_chinese_and_hides_raw_fields(self):
        self.role_data = {'area': 36, 'name': '37943815', 'level': 6, 'exp': 807,
                          'icon': 'http://example.test/avatar', 'tdmLevel': 1, 'tdmExp': 0,
                          'propcapital': 378526, 'hafcoinnum': 3645677, 'totalPrice': 4078540,
                          'noncurrentAsset': 54337, 'currentAsset': 4024203, 'unknown': 'hidden'}
        self.owners['bound'] = 'user-a'
        await self.plugin.save_binding('user-a', 'bound', 'qq')
        await self.command('三角洲 信息')
        text = self.sender.send_message.await_args.args[0]
        self.assertIn('角色名：37943815', text)
        self.assertIn('哈夫币：3,645,677', text)
        self.assertIn('物资价值：378,526', text)
        self.assertIn('总资产：4,078,540', text)
        self.assertIn('流动资产：4,024,203', text)
        self.assertIn('固定资产：54,337', text)
        self.assertIn('全面战场经验：0', text)
        for raw in ('area', 'icon', 'http://', 'tdmLevel', 'hafcoinnum', 'unknown', 'exp'):
            self.assertNotIn(raw, text)

    def test_daily_parser_rejects_missing_date_and_keeps_leading_zero(self):
        today = datetime.now(TZ).date()
        self.assertEqual(parse_passwords(self.daily_data, today)[0][1], '0012')
        self.daily_data['data']['update_date'] = '昨天'
        with self.assertRaises(ValueError):
            parse_passwords(self.daily_data, today)

    async def test_boundaries_no_slash_and_pending_cancellation(self):
        for text in ('三角洲玩家', '/三角洲 登录', '聊聊三角洲 登录'):
            self.assertFalse(await self.command(text))
        self.assertEqual(self.plugin.command_capabilities.slash_commands, ())
        await self.command('三角洲 登录')
        pending = next(iter(self.plugin.pages.values()))
        await self.plugin.on_unload()
        self.assertTrue(pending['terminal'])
        self.assertIn('取消', self.sender.send_private_message.await_args.args[1])

    async def test_zip_loads_through_real_plugin_runtime(self):
        from app.infrastructure.plugin_runtime import loader, package_store
        from app.infrastructure.runtime import PluginRuntime

        root = Path(self.tmp.name) / 'zip-runtime'
        root.mkdir()
        data = root / 'data'
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            for source in (ROOT / 'plugins/delta_force').iterdir():
                if source.is_file():
                    archive.writestr('delta_force/' + source.name, source.read_bytes())
        with (
            patch.object(loader, '_PROJECT_ROOT', str(root)),
            patch.object(package_store, 'DATA_DIR', str(data)),
            patch('core.paths.DATA_DIR', str(data)),
            patch('web.web_player.app', FastAPI()),
        ):
            runtime = PluginRuntime(state_path=data / 'state.json')
            try:
                result = await runtime.install_zip(output.getvalue(), handler=self.host)
                self.assertTrue(result.ok, result.message)
                self.assertTrue(runtime.matches_plain_command('三角洲 登录'))
                self.assertTrue((data / 'plugins/delta_force/login.html').is_file())
                self.assertTrue(await runtime.try_dispatch_mention('三角洲 帮助', 'c', 'a', 'u', self.host))
            finally:
                await runtime.stop()

    async def test_private_delivery_failure_never_leaks_link_to_channel(self):
        self.sender.send_private_message.return_value = {'error': 'private disabled'}
        with patch.object(module.asyncio, 'sleep', new=AsyncMock()):
            await self.command('三角洲 登录')
        self.assertFalse(self.plugin.pages)
        self.assertFalse(self.plugin.tasks)
        message = self.sender.send_message.await_args.args[0]
        self.assertNotIn(PREFIX, message)
        self.assertIn('私信', message)

    def test_signing_and_url_validation(self):
        headers = sign_headers('k', 's', 'u', 'GET', '/x?a=1', timestamp=1, nonce='n')
        canonical = 'GET\n/x?a=1\n1\nn\nu\n' + hashlib.sha256(b'').hexdigest()
        self.assertEqual(headers['X-Signature'], hmac.new(b's', canonical.encode(), hashlib.sha256).hexdigest())
        for url in ('file:///etc/passwd', 'http://user:pass@host', 'http://host/path', 'http://host?key=x'):
            with self.assertRaises(ValueError):
                Client(url, 'key', 'secret')

    def test_plugin_can_load_on_previous_host_without_plain_prefix_support(self):
        @dataclass
        class PreviousCapabilities:
            mention_prefixes: tuple[str, ...] = ()
            slash_commands: tuple[str, ...] = ()
            is_public_command: bool = True

        with patch.object(module, 'PluginCommandCapabilities', PreviousCapabilities):
            capabilities = self.plugin.command_capabilities
        self.assertEqual(capabilities.mention_prefixes, ('三角洲',))
        self.assertEqual(capabilities.slash_commands, ())


if __name__ == '__main__':
    unittest.main()
