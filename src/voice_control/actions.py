"""Voice commands reuse the existing music operations, with per-user confirmations."""
from __future__ import annotations

import re
import time
from typing import Any

from core.redis_keys import VOLUME
from domain.playback import AreaId, AreaWebCommand, GlobalWebCommand

from .intent import CATALOG, normalize, parse_command


def comparable(value):
    return re.sub(r'[^\w]', '', str(value)).casefold()


class MusicActions:
    def __init__(self, music: Any, sender: Any, area: str, voice_channel: str, text_channel: str):
        self.music, self.sender = music, sender
        self.area, self.voice_channel, self.text_channel = area, voice_channel, text_channel
        self.pending: dict[str, tuple[float, int, list[dict]]] = {}
        self.previous_volume = None

    def current(self):
        return (self.music._voice_channel_area, self.music._voice_channel_id) == (self.area, self.voice_channel)

    async def say(self, text):
        await self.sender.send_message(text, channel=self.text_channel, area=self.area)

    async def run(self, text, user):
        if not self.current():
            return
        command = parse_command(text)
        pending = self.pending.get(user)
        clean = normalize(text).replace(' ', '')
        choice = re.fullmatch(r'(?:选择|播放)?第?([一二三123])首?', clean)
        if not pending and (choice or clean in ('确认', '就这首')):
            await self.say('当前没有待确认的歌曲，请先说出歌名。')
            return
        if pending and (choice or clean in ('确认', '就这首')):
            expires, generation, songs = self.pending.pop(user)
            if expires < time.monotonic() or generation != self.music._playback_generation:
                await self.say('选歌确认已过期，请重新说出歌名。')
                return
            index = {'一': 0, '二': 1, '三': 2, '1': 0, '2': 1, '3': 2}[choice[1]] if choice else 0
            if index >= len(songs):
                await self.say('没有这个选项，请重新点歌。')
                return
            await self.music.play_song_choice(songs[index], self.text_channel, self.area, user, expected_voice_channel=self.voice_channel)
            return
        if not command['recognized']:
            await self.say(command['summary'])
            return
        action, params = command['action'], command['parameters']
        if action == 'cancel':
            self.pending.pop(user, None)
            await self.say('已取消本次语音输入。')
            return
        if action == 'help':
            await self.say('语音命令：\n' + '\n'.join(' / '.join(row['examples'][:2]) for row in CATALOG))
            return
        if action == 'play':
            generation = self.music._playback_generation
            songs = await self.music.search_candidates(params['query'], params['platform'], limit=3)
            if not self.current() or generation != self.music._playback_generation:
                return
            for song in songs:
                song['platform'] = params['platform']
            exact = [song for song in songs
                     if comparable(song.get('name')) == comparable(params['song'])
                     and (not params['artist'] or comparable(params['artist']) in comparable(song.get('artists')))]
            if len(songs) == 1 and len(exact) == 1 and not command['needs_confirmation']:
                await self.music.play_song_choice(songs[0], self.text_channel, self.area, user, expected_voice_channel=self.voice_channel)
                return
            if not songs:
                await self.say('没有找到匹配歌曲，请重说歌名和歌手。')
                return
            self.pending[user] = (time.monotonic() + 45, generation, songs)
            if len(self.pending) > 64:
                self.pending.pop(next(iter(self.pending)))
            await self.say('请再次喊 GG Bot 后说“第一首 / 第二首 / 第三首”确认，45 秒内有效：\n' +
                           '\n'.join(f'{index}. {song.get("name")} · {song.get("artists")}'
                                     for index, song in enumerate(songs, 1)))
            return
        self.pending.pop(user, None)
        if action == 'random_liked':
            await self.music.play_liked(self.text_channel, self.area, user, expected_voice_channel=self.voice_channel)
        elif action == 'next':
            await self.music.play_next(self.text_channel, self.area, user, expected_voice_channel=self.voice_channel)
        elif action == 'queue':
            await self.music.show_queue(self.text_channel, self.area)
        elif action == 'current':
            song = await self.music._get_queue(self.area).get_current()
            await self.say(f'正在播放：{song.get("name")} · {song.get("artists")}' if song else '当前没有播放歌曲。')
        elif action in ('pause', 'resume', 'stop'):
            result = await self.music._execute_web_command(
                AreaWebCommand(AreaId(self.area), action, {}), queue=self.music._get_queue(self.area), expected_voice_channel=self.voice_channel)
            if result:
                await self.say(command['summary'])
        elif action in ('clear_queue', 'mode'):
            async with self.music._playback_lock:
                if not self.current():
                    return
                queue = self.music._get_queue(self.area)
                if action == 'clear_queue':
                    await queue.clear_queue()
                else:
                    await self.music.set_play_mode(params['mode'], queue=queue)
            await self.say(command['summary'])
        elif action in ('volume_set', 'volume_adjust', 'mute', 'unmute'):
            # Execute under the playback lock so a channel switch cannot retarget a global volume change.
            async with self.music._playback_lock:
                if not self.current():
                    return
                redis = await self.music._get_queue(self.area).client()
                value = await redis.get(VOLUME)
                current = max(0, min(100, int(value if value is not None else 50)))
                if action == 'mute':
                    if current:
                        self.previous_volume = current
                    volume = 0
                elif action == 'unmute':
                    volume = self.previous_volume if self.previous_volume is not None else current
                else:
                    volume = params['value'] if action == 'volume_set' else current + params['delta']
                volume = max(0, min(100, volume))
                await self.music._execute_web_command(GlobalWebCommand('volume', {'value': volume}))
            await self.say(f'音量：{volume}')
