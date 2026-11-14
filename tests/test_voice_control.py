import asyncio
import base64
import json
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from aiohttp.test_utils import TestServer

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from domain.plugins.base import PluginConfig  # noqa: E402
from plugins.voice_control import VoiceControl  # noqa: E402
from tools.voice_asr_server import create_app  # noqa: E402
from voice_control.actions import MusicActions  # noqa: E402
from voice_control.asr import LocalASR  # noqa: E402
from voice_control.recognition import Listener  # noqa: E402
from voice_control.service import VoiceControlService  # noqa: E402


class Decoder:
    def AcceptWaveform(self, pcm):
        return False

    def PartialResult(self):
        return json.dumps({'partial': 'gg bot'})


class ListenerTests(unittest.TestCase):
    def test_wake_capture_timeout_and_repeated_reset(self):
        now = [10.]
        model = SimpleNamespace(decoder=Decoder)
        listener = Listener(model, lambda: now[0])
        wake = listener.feed(bytes(3200))
        assert wake is not None
        self.assertEqual(wake[0], 'wake')
        self.assertIsNone(listener.feed(bytes(3200)))
        listener.ready()
        listener.feed(b'\x00\x10' * 1600)
        listener.feed(b'\x00\x10' * 1600)
        event = None
        for _ in range(12):
            event = listener.feed(bytes(3200)) or event
        assert event is not None
        self.assertEqual(event[0], 'audio')
        self.assertLessEqual(len(event[1]), 320000)
        wake = listener.feed(bytes(3200))
        assert wake is not None
        self.assertEqual(wake[0], 'wake')
        listener.ready()
        now[0] = 21
        timeout = listener.tick()
        assert timeout is not None
        self.assertEqual(timeout[0], 'timeout')
        self.assertEqual(listener.state, 'wake')
        with self.assertRaises(ValueError):
            listener.feed(b'x')


class ASRTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_transport_auth_pcm_limits_and_hotwords(self):
        worker = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(worker.shutdown)
        recognize = Mock(return_value={'text': '播放晴天'})
        server = TestServer(create_app(recognize, 'test-secret', worker))
        await server.start_server()
        self.addAsyncCleanup(server.close)
        client = LocalASR(str(server.make_url('/')).rstrip('/'), 'test-secret', ['晴天'])
        self.addAsyncCleanup(client.close)
        self.assertEqual(await client.transcribe(b'\x00\x10' * 1600), '播放晴天')
        recognize.assert_called_once_with(b'\x00\x10' * 1600, ['晴天'])
        empty = LocalASR(str(server.make_url('/')).rstrip('/'), 'test-secret', '')
        self.addAsyncCleanup(empty.close)
        self.assertEqual(await empty.transcribe(b'\x00\x10' * 1600), '播放晴天')
        self.assertEqual(recognize.call_args.args[1], [])
        async with aiohttp.ClientSession() as http:
            async with http.post(server.make_url('/transcribe'), json={}) as response:
                self.assertEqual(response.status, 401)
            async with http.post(server.make_url('/transcribe'), headers={'Authorization': 'Bearer test-secret'},
                                 json={'sample_rate': 16000, 'audio': base64.b64encode(b'x').decode()}) as response:
                self.assertEqual(response.status, 400)

    async def test_disabled_plugin_does_not_start_or_touch_voice(self):
        plugin = VoiceControl()
        host = Mock()
        await plugin.on_load(host, PluginConfig('voice_control', {}, '', False))
        host.assert_not_called()
        self.assertIsNone(plugin.task)
        self.assertIsNone(plugin.service)
        await plugin.on_unload()
        with self.assertRaises(ValueError):
            await plugin.on_load(host, PluginConfig('voice_control', {'enabled': True}, '', False))

    async def test_independent_wake_volume_defaults_and_bounds(self):
        spec = VoiceControl().config_spec
        self.assertEqual(spec.to_example()['wake_volume'], 150)
        for value in (0, 75, 150, 200):
            self.assertEqual(spec.apply(PluginConfig('voice_control', {'wake_volume': str(value)}, '', True))['wake_volume'], value)
        for value in (-1, 201):
            with self.assertRaises(ValueError):
                spec.apply(PluginConfig('voice_control', {'wake_volume': value}, '', True))

    async def test_plugin_automatically_resolves_its_bundled_wake_model(self):
        service = SimpleNamespace(start=AsyncMock(), run=AsyncMock(), close=AsyncMock())
        plugin = VoiceControl()
        with patch('voice_control.service.VoiceControlService', return_value=service) as factory:
            await plugin.on_load(Mock(), PluginConfig('voice_control', {
                'enabled': True, 'area': 'a', 'voice_channel': 'v', 'text_channel': 't',
                'wake_model_path': '', 'hotwords': '', 'allowed_uids': '',
            }, '', True))
            settings = factory.call_args.args[1]
            self.assertEqual(Path(settings['wake_model_path']), ROOT / 'plugins/voice_control/models/vosk-model-small-en-us-0.15')
            self.assertEqual(settings['hotwords'], [])
            self.assertEqual(settings['wake_volume'], 150)
            await plugin.on_unload()


class ActionsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.queue = AsyncMock()
        self.redis = AsyncMock()
        self.redis.get.return_value = '95'
        self.queue.client.return_value = self.redis
        self.music = SimpleNamespace(
            _voice_channel_area='a', _voice_channel_id='v', _playback_generation=1,
            _playback_lock=asyncio.Lock(), _get_queue=Mock(return_value=self.queue),
            search_candidates=AsyncMock(), play_song_choice=AsyncMock(), play_next=AsyncMock(),
            play_liked=AsyncMock(), show_queue=AsyncMock(), set_play_mode=AsyncMock(),
            _execute_web_command=AsyncMock(return_value=True))
        self.sender = SimpleNamespace(send_message=AsyncMock())
        self.actions = MusicActions(self.music, self.sender, 'a', 'v', 'text')

    async def test_exact_play_and_same_user_confirmation(self):
        song = {'id': 1, 'name': '晴天', 'artists': '周杰伦'}
        self.music.search_candidates.return_value = [song]
        await self.actions.run('播放周杰伦的晴天', 'u')
        self.music.play_song_choice.assert_awaited_once_with(song, 'text', 'a', 'u', expected_voice_channel='v')
        self.music.play_song_choice.reset_mock()
        self.music.search_candidates.return_value = [song, {'id': 2, 'name': '晴天', 'artists': '另一个人'}]
        await self.actions.run('播放晴天', 'u')
        self.music.play_song_choice.assert_not_awaited()
        await self.actions.run('第二首', 'other')
        self.music.play_song_choice.assert_not_awaited()
        await self.actions.run('第二首', 'u')
        self.assertEqual(self.music.play_song_choice.await_args.args[0]['id'], 2)

    async def test_five_candidates_support_spoken_fifth_choice(self):
        songs = [{'id': i, 'name': 'song' + str(i), 'artists': 'artist'} for i in range(1, 7)]
        self.music.search_candidates.return_value = songs
        await self.actions.run('播放晴天', 'u')
        self.music.search_candidates.assert_awaited_once_with('晴天', 'netease', limit=5)
        self.assertEqual(len(self.actions.pending['u'][2]), 5)
        await self.actions.run('第五首', 'u')
        self.assertEqual(self.music.play_song_choice.call_args.args[0]['id'], 5)
        self.assertNotIn('u', self.actions.pending)

    async def test_stale_search_result_and_stale_confirmation_are_dropped(self):
        song = {'id': 1, 'name': '晴天', 'artists': '周杰伦'}
        async def switch(*args, **kwargs):
            self.music._voice_channel_id = 'other'
            return [song]
        self.music.search_candidates.side_effect = switch
        await self.actions.run('播放晴天', 'u')
        self.music.play_song_choice.assert_not_awaited()
        self.music._voice_channel_id = 'v'
        self.actions.pending['u'] = (1e20, 0, [song])
        await self.actions.run('确认', 'u')
        self.music.play_song_choice.assert_not_awaited()

    async def test_controls_volume_clamp_mute_restore_and_cancel(self):
        await self.actions.run('声音大一点', 'u')
        self.assertEqual(self.music._execute_web_command.await_args.args[0].payload['value'], 100)
        await self.actions.run('静音', 'u')
        self.assertEqual(self.music._execute_web_command.await_args.args[0].payload['value'], 0)
        self.redis.get.return_value = '0'
        await self.actions.run('取消静音', 'u')
        self.assertEqual(self.music._execute_web_command.await_args.args[0].payload['value'], 95)
        await self.actions.run('暂停', 'u')
        self.assertEqual(self.music._execute_web_command.await_args.args[0].action, 'pause')
        await self.actions.run('随机播放队列', 'u')
        self.music.set_play_mode.assert_awaited_once_with('shuffle', queue=self.queue)
        await self.actions.run('清空队列', 'u')
        self.queue.clear_queue.assert_awaited_once()
        self.actions.pending['u'] = (1e20, 1, [])
        await self.actions.run('取消', 'u')
        self.assertNotIn('u', self.actions.pending)

    async def test_wrong_channel_and_negation_never_execute(self):
        await self.actions.run('不要播放晴天', 'u')
        self.music.search_candidates.assert_not_awaited()
        self.music._voice_channel_id = 'different'
        await self.actions.run('音量调到零', 'u')
        self.music._execute_web_command.assert_not_awaited()


class ChannelGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_standby_cannot_steal_new_cross_area_connection(self):
        from music.music import MusicHandler
        handler = MusicHandler.__new__(MusicHandler)
        handler.voice = SimpleNamespace(available=True, join=AsyncMock())
        handler._voice_lock = asyncio.Lock()
        handler._voice_channel_id = None
        handler._voice_channel_area = None
        handler._leave_current_voice_channel = AsyncMock()
        await handler._voice_lock.acquire()
        pending = asyncio.create_task(handler.enter_standby_voice_channel('standby', 'home'))
        await asyncio.sleep(0)
        # The text-song request finishes its join while standby is waiting.
        handler._voice_channel_id = 'lol'
        handler._voice_channel_area = 'another-area'
        handler._voice_lock.release()
        result = await pending
        self.assertFalse(result['status'])
        self.assertEqual(handler._voice_channel_id, 'lol')
        self.assertEqual(handler._voice_channel_area, 'another-area')
        handler.voice.join.assert_not_awaited()
        handler._leave_current_voice_channel.assert_not_awaited()

    async def test_web_and_voice_stop_stays_only_in_standby_channel(self):
        from music.music_web_control import WebControlExecutor
        h = SimpleNamespace(_voice_wake_standby=('a', 'v'), _voice_channel_area='a',
                            _voice_channel_id='v', _advance_playback_generation_locked=Mock(),
                            voice=SimpleNamespace(available=True, stop_audio=AsyncMock()),
                            _leave_current_voice_channel=AsyncMock())
        executor = WebControlExecutor(h)
        queue = AsyncMock()
        await executor._handle_stop(queue)
        h.voice.stop_audio.assert_awaited_once()
        h._leave_current_voice_channel.assert_not_awaited()
        queue.clear_queue.assert_awaited_once()
        h._voice_channel_id = 'other'
        await executor._handle_stop(queue)
        h._leave_current_voice_channel.assert_awaited_once()

    async def test_remote_speaker_uses_only_bound_channel_without_account_lookup(self):
        from domain.playback.voice_participant import VoiceParticipant
        from music.music import MusicHandler
        handler = SimpleNamespace(voice=SimpleNamespace(available=True),
                                  sender=SimpleNamespace(get_voice_channel_for_user=AsyncMock()),
                                  _voice_channel_area='a', _voice_channel_id='v')
        user = VoiceParticipant('123', 'a', 'v')
        self.assertTrue(await MusicHandler._check_and_enter_voice_channel(
            cast(Any, handler), user, 'text', 'a', expected_voice_channel='v'))
        handler._voice_channel_id = 'other'
        self.assertFalse(await MusicHandler._check_and_enter_voice_channel(
            cast(Any, handler), user, 'text', 'a', expected_voice_channel='v'))
        handler.sender.get_voice_channel_for_user.assert_not_awaited()

    async def test_late_playback_and_controls_cannot_follow_another_channel(self):
        from domain.playback import AreaId, AreaWebCommand
        from music.music import MusicHandler
        handler = MusicHandler.__new__(MusicHandler)
        handler._playback_lock = asyncio.Lock()
        handler._voice_channel_area = 'a'
        handler._voice_channel_id = 'other'
        handler._playback_generation = 1
        handler.voice = SimpleNamespace(available=True, pause_audio=AsyncMock())
        handler.sender = SimpleNamespace(get_voice_channel_for_user=AsyncMock(return_value='other'),
                                         send_message=AsyncMock())
        self.assertFalse(await handler._check_and_enter_voice_channel('u', 'text', 'a', expected_voice_channel='v'))
        result = await handler._commit_song_request({'area': 'a'}, expected_voice_channel='v')
        self.assertEqual(result['code'], 'playback_session_changed')
        await handler.play_next('text', 'a', 'u', expected_voice_channel='v')
        self.assertFalse(await handler._execute_web_command(AreaWebCommand(AreaId('a'), 'pause', {}),
                                                           queue=AsyncMock(), expected_voice_channel='v'))
        handler.voice.pause_audio.assert_not_awaited()


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.voice = SimpleNamespace(configure_voice_capture=AsyncMock(return_value={'ok': True}),
                                     play_wake_tone=AsyncMock(return_value={'ok': True, 'after': 1000}),
                                     drain_voice_audio=AsyncMock())
        self.music = SimpleNamespace(voice=self.voice, _voice_channel_area='a', _voice_channel_id='v',
                                     _playback_generation=1, enter_voice_channel=AsyncMock(),
                                     enter_standby_voice_channel=AsyncMock())
        self.raw = SimpleNamespace(get_voice_channel_members=AsyncMock(return_value={'v': [{'uid': 'u'}]}),
                                   get_person_infos_batch=AsyncMock(return_value={'u': {'pid': 123}}),
                                   get_voice_channel_for_user_strict=AsyncMock(return_value='v'))
        host = SimpleNamespace(music=self.music, sender=SimpleNamespace(raw=self.raw, send_message=AsyncMock()))
        self.settings = {'area': 'a', 'voice_channel': 'v', 'text_channel': 'text', 'allowed_uids': []}
        self.service = VoiceControlService(host, self.settings)
        self.action_run = AsyncMock()
        patcher = patch.object(self.service.actions, 'run', self.action_run)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_gateway_keepalive_write_reaches_real_music_handler(self):
        host = SimpleNamespace(music=SimpleNamespace(handler=self.music), sender=self.service.sender)
        service = VoiceControlService(host, self.settings)
        self.assertIs(service.music, self.music)
        self.music._voice_wake_channel = ('a', 'v')
        await service.close()
        self.assertIsNone(self.music._voice_wake_channel)

    async def test_text_number_selects_only_own_voice_candidates_in_same_channel(self):
        from domain.playback.voice_participant import VoiceParticipant
        key = VoiceParticipant('123', 'a', 'v')
        self.service.actions.pending[key] = (1e20, 1, [{'id': 5}])
        self.raw.get_person_detail_full = AsyncMock(return_value={'pid': '123'})
        self.assertFalse(await self.service.select_text('5', 'wrong', 'a', 'u'))
        self.raw.get_person_detail_full.assert_not_awaited()
        self.raw.get_person_detail_full.return_value = {'pid': 'other'}
        self.assertFalse(await self.service.select_text('5', 'text', 'a', 'other'))
        self.action_run.assert_not_awaited()
        self.raw.get_person_detail_full.return_value = {'pid': '123'}
        self.assertTrue(await self.service.select_text('5', 'text', 'a', 'u'))
        self.action_run.assert_awaited_once_with('5', key)

    async def test_follow_channel_clears_previous_capture_and_candidates(self):
        self.service.capture = True
        self.service.listeners = {'123': Mock()}
        self.service.owner = '123'
        self.service.actions.pending['voice:123'] = (0, 1, [])
        old_actions = self.service.actions
        self.music._voice_channel_id = 'new'
        self.voice.drain_voice_audio.return_value = {'generation': 2, 'users': [], 'frames': []}
        await self.service.poll()
        self.assertEqual(self.service.actions.voice_channel, 'new')
        self.assertEqual(self.music._voice_wake_channel, ('a', 'new'))
        self.assertEqual(old_actions.pending, {})
        self.assertEqual(self.service.listeners, {})
        self.assertIsNone(self.service.owner)
        self.voice.configure_voice_capture.assert_any_await(False)
        self.voice.configure_voice_capture.assert_any_await(True)
        user = await self.service.resolve_user('123')
        from domain.playback.voice_participant import VoiceParticipant
        assert isinstance(user, VoiceParticipant)
        self.assertEqual(user.channel, 'new')

    async def test_stopping_returns_to_standby_even_after_initial_join(self):
        self.service.joined_once = True
        self.music._voice_channel_id = None
        self.music._voice_channel_area = None
        self.music.enter_standby_voice_channel.return_value = {'status': True}
        await self.service.poll()
        self.music.enter_standby_voice_channel.assert_awaited_once_with('v', 'a')

    async def test_does_not_pull_bot_back_from_another_area(self):
        self.music._voice_channel_id = 'elsewhere'
        self.music._voice_channel_area = 'other-area'
        await self.service.poll()
        self.music.enter_voice_channel.assert_not_awaited()
        self.voice.configure_voice_capture.assert_not_awaited()

    async def test_cross_area_follow_uses_request_text_channel_and_speaker_scope(self):
        from domain.playback.voice_participant import VoiceParticipant
        old = self.service.actions
        old.pending['voice:123'] = (0, 1, [])
        self.music._voice_channel_area = 'other-area'
        self.music._voice_channel_id = 'other-voice'
        self.music._voice_text_channels = {'other-area': 'other-text'}
        self.voice.drain_voice_audio.return_value = {'generation': 2, 'users': [], 'frames': []}
        await self.service.poll()
        actions = self.service.actions
        self.assertEqual((actions.area, actions.voice_channel, actions.text_channel),
                         ('other-area', 'other-voice', 'other-text'))
        self.assertEqual(self.music._voice_wake_channel, ('other-area', 'other-voice'))
        self.assertEqual(old.pending, {})
        self.assertTrue(self.service.capture)
        user = await self.service.resolve_user('123')
        assert isinstance(user, VoiceParticipant)
        self.assertEqual((user.area, user.channel), ('other-area', 'other-voice'))
        await actions.say('test')
        self.service.sender.send_message.assert_awaited_once_with('test', channel='other-text', area='other-area')
        self.music.enter_standby_voice_channel.assert_not_awaited()

    async def test_slow_asr_cannot_run_in_new_area(self):
        async def transcribe(pcm):
            self.music._voice_channel_area = 'other-area'
            self.music._voice_channel_id = 'other-voice'
            return '停止播放'
        with patch.object(self.service, 'asr', SimpleNamespace(transcribe=AsyncMock(side_effect=transcribe))):
            await self.service.handle('123', ('audio', bytes(3200)))
        self.action_run.assert_not_awaited()

    async def test_identity_resolution_is_channel_bound_and_allowlisted(self):
        self.settings['allowed_uids'] = ['u']
        self.assertEqual(await self.service.resolve_user('123'), 'u')
        self.settings['allowed_uids'] = ['other']
        self.assertIsNone(await self.service.resolve_user('123'))
        self.settings['allowed_uids'] = ['u']
        self.raw.get_voice_channel_for_user_strict.return_value = 'other'
        self.assertIsNone(await self.service.resolve_user('123'))

    async def test_slow_asr_cannot_execute_after_switch_or_member_leaves(self):
        self.settings['allowed_uids'] = ['u']
        async def transcribe(pcm):
            self.music._voice_channel_id = 'other'
            return '停止播放'
        fake_asr = SimpleNamespace(transcribe=AsyncMock(side_effect=transcribe))
        patcher = patch.object(self.service, 'asr', fake_asr)
        patcher.start()
        self.addCleanup(patcher.stop)
        await self.service.handle('123', ('audio', bytes(3200)))
        self.action_run.assert_not_awaited()
        self.music._voice_channel_id = 'v'
        async def leave(pcm):
            self.raw.get_voice_channel_for_user_strict.return_value = None
            return '停止播放'
        fake_asr.transcribe.side_effect = leave
        await self.service.handle('123', ('audio', bytes(3200)))
        self.action_run.assert_not_awaited()

    async def test_default_allows_remote_speaker_without_account_lookup(self):
        from domain.playback.voice_participant import VoiceParticipant
        user = await self.service.resolve_user('123')
        self.assertIsInstance(user, VoiceParticipant)
        self.assertEqual(str(user), 'voice:123')
        self.raw.get_voice_channel_members.assert_not_awaited()
        self.raw.get_person_infos_batch.assert_not_awaited()
        self.service.listeners['123'] = Mock()
        await self.service.handle('123', ('wake', b''))
        self.voice.play_wake_tone.assert_awaited_once()
        self.assertEqual(self.voice.play_wake_tone.call_args.args, (150,))

    async def test_only_woken_speaker_audio_is_processed_and_close_disables_capture(self):
        self.service.capture = True
        self.service.generation = 1
        decoder = Mock()
        decoder.tick.return_value = None
        decoder.feed.return_value = None
        self.service.listeners = {'123': decoder}
        self.service.owner = '123'
        self.voice.drain_voice_audio.return_value = {'generation': 1, 'users': ['123', '456'], 'frames': [
            {'uid': '456', 'generation': 1, 'at': 5000, 'pcm': base64.b64encode(bytes(3200)).decode()},
            {'uid': '123', 'generation': 1, 'at': 5000, 'pcm': base64.b64encode(bytes(3200)).decode()}]}
        await self.service.poll()
        decoder.feed.assert_called_once_with(bytes(3200))
        await self.service.close()
        self.voice.configure_voice_capture.assert_awaited_once_with(False)
        self.assertIsNone(self.music._voice_wake_channel)
