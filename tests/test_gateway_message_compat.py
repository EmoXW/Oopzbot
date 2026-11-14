import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from oopz.sdk_gateway import AsyncOopzGateway  # noqa: E402
from oopz_sdk.exceptions import OopzApiError  # noqa: E402
from oopz_sdk.models import ImageAttachment  # noqa: E402


class MessageCompatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gateway = cast(Any, AsyncOopzGateway.__new__(AsyncOopzGateway))
        self.send = AsyncMock(return_value=SimpleNamespace(message_id='sent', timestamp='123'))
        self.gateway.bot = SimpleNamespace(messages=SimpleNamespace(send_message=self.send))
        self.gateway._style_tags = Mock(return_value=[])
        self.gateway._schedule_auto_recall = AsyncMock()

    async def test_rejected_legacy_message_retries_v2_with_cover_and_link(self):
        self.send.side_effect = [OopzApiError('暂不支持发送此类型消息'),
                                 SimpleNamespace(message_id='sent', timestamp='123')]
        cover = ImageAttachment.from_manually(file_key='cover', url='https://example.com/cover.png', width=320, height=320)
        text = '歌曲与专辑\nhttps://example.com/player'
        await self.gateway.send_message(text, area='other-area', channel='other-text', attachments=[cover])
        self.assertEqual(self.send.await_count, 2)
        first, second = self.send.await_args_list
        self.assertEqual(first.kwargs['version'], 'v1')
        self.assertEqual(second.kwargs['version'], 'v2')
        self.assertEqual(first.args, second.args)
        self.assertEqual(second.kwargs['area'], 'other-area')
        self.assertEqual(second.kwargs['channel'], 'other-text')
        self.assertEqual(second.kwargs['attachments'], [cover])
        self.gateway._schedule_auto_recall.assert_awaited_once_with('sent', 'other-area', 'other-text', '123')

    async def test_successful_legacy_send_is_not_repeated(self):
        await self.gateway.send_message('text', area='a', channel='t', auto_recall=False)
        self.send.assert_awaited_once()

    async def test_other_errors_and_explicit_version_are_not_retried(self):
        for error, kwargs in [(OopzApiError('禁止发送消息'), {}), (TimeoutError(), {}),
                              (OopzApiError('暂不支持发送此类型消息'), {'version': 'v1'}),
                              (OopzApiError('暂不支持发送此类型消息'), {'version': 'v2'})]:
            with self.subTest(error=type(error).__name__, kwargs=kwargs):
                self.send.reset_mock()
                self.send.side_effect = error
                with self.assertRaises(type(error)):
                    await self.gateway.send_message('text', area='a', channel='t', **kwargs)
                self.send.assert_awaited_once()
