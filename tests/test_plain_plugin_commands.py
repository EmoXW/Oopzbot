import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from app.infrastructure.runtime import PluginRuntime
from app.services.routing.command_access_service import CommandAccessService
from app.services.routing.command_message_service import CommandMessageService, MessageContext
from app.services.routing.command_router import CommandRouter
from domain.plugins.base import BotModule, PluginCommandCapabilities, PluginMetadata


class Example(BotModule):
    public = True

    @property
    def metadata(self):
        return PluginMetadata('example', 'example')

    @property
    def command_capabilities(self):
        return PluginCommandCapabilities(mention_prefixes=('三角洲',), plain_prefixes=('三角洲',), is_public_command=self.public)


class PlainPluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.plugins = PluginRuntime()
        self.plugin = Example()
        await self.plugins.registry.register(self.plugin)
        self.runtime: Any = SimpleNamespace(
            plugins=self.plugins, bot_mention='@bot', sender=SimpleNamespace(send_message=AsyncMock()),
            services=SimpleNamespace(routing=SimpleNamespace(mention=SimpleNamespace(dispatch=AsyncMock())),
                                     safety=SimpleNamespace(recall_scheduler=SimpleNamespace(schedule_user_message_recall=AsyncMock()))),
        )
        self.access = CommandAccessService(self.runtime)
        self.runtime.services.routing.access = self.access

    async def test_plain_prefix_boundaries_and_unloaded_plugin(self):
        for text in ('三角洲', '三角洲 登录', '三角洲\t登录 wx'):
            self.assertTrue(self.plugins.matches_plain_command(text))
        for text in ('三角洲玩家', '聊聊三角洲 登录', '/三角洲 登录'):
            self.assertFalse(self.plugins.matches_plain_command(text))
        await self.plugins.registry.unregister('example')
        self.assertFalse(self.plugins.matches_plain_command('三角洲 登录'))

    async def test_permission_gate_and_both_routes(self):
        context = MessageContext.from_message({'content': '三角洲 登录', 'person': 'someone'})
        with patch.object(self.access, 'is_admin', return_value=False):
            self.assertFalse(await CommandMessageService(self.runtime).reject_unauthorized_command(context))
        await CommandRouter(self.runtime).route(context)
        self.runtime.services.routing.mention.dispatch.assert_awaited_once_with('三角洲 登录', '', '', 'someone')
        self.runtime.services.routing.mention.dispatch.reset_mock()
        context = MessageContext.from_message({'content': '@bot 三角洲 登录', 'person': 'someone'})
        await CommandRouter(self.runtime).route(context)
        self.runtime.services.routing.mention.dispatch.assert_awaited_once_with('三角洲 登录', '', '', 'someone')

    async def test_admin_plugin_cannot_bypass_permission_gate(self):
        self.plugin.public = False
        context = MessageContext.from_message({'content': '三角洲 登录', 'person': 'someone'})
        with patch.object(self.access, 'is_admin', return_value=False):
            self.assertTrue(await CommandMessageService(self.runtime).reject_unauthorized_command(context))
        self.runtime.sender.send_message.assert_awaited_once()
