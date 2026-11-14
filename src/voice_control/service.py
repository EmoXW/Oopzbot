"""Follow the bot across areas, returning to its configured standby channel."""
from __future__ import annotations

import asyncio
import base64
import time

from core.logger_config import get_logger
from domain.playback.voice_participant import VoiceParticipant

from .actions import MusicActions
from .asr import LocalASR
from .recognition import Listener, WakeModel

LOG = get_logger('VoiceControl')


class VoiceControlService:
    def __init__(self, host, settings):
        self.host, self.settings = host, settings
        # PluginHost exposes a lazy gateway; writes must reach its real music handler.
        self.music, self.sender = getattr(host.music, 'handler', host.music), host.sender
        self.voice = self.music.voice
        self.actions = MusicActions(self.music, self.sender, settings['area'], settings['voice_channel'], settings['text_channel'])
        self.asr = None
        self.model = None
        self.listeners = {}
        self.owner = None
        self.after = 0
        self.generation = None
        self.capture = False
        self.joined_once = False
        self.next_join = 0.0
        self.muted_volume = None
        self._diagnostic_at = 0.0
        self._received_frames = 0

    def in_channel(self):
        return self.actions.current()

    async def start(self):
        if self.voice is None:
            raise ValueError('语音后端不可用')
        self.model = await asyncio.to_thread(WakeModel, self.settings['wake_model_path'])
        self.asr = LocalASR(self.settings['asr_url'], self.settings['asr_token'], self.settings['hotwords'])
        self.music._voice_wake_channel = (self.settings['area'], self.settings['voice_channel'])
        self.music._voice_wake_standby = (self.settings['area'], self.settings['voice_channel'])

    async def resolve_user(self, rtc_uid):
        if not self.in_channel():
            return None
        if not self.settings['allowed_uids']:
            return VoiceParticipant(str(rtc_uid), self.actions.area, self.actions.voice_channel)
        raw = getattr(self.sender, 'raw', self.sender)
        members = await raw.get_voice_channel_members(area=self.actions.area)
        users = [str(item.get('uid') or '') for item in members.get(self.actions.voice_channel, [])
                 if not item.get('isBot')]
        users = [user for user in users if user]
        if not users:
            return None
        profiles = await raw.get_person_infos_batch(users[:100])
        matches = [user for user in users if str(profiles.get(user, {}).get('pid', '')) == str(rtc_uid)]
        if len(matches) != 1:
            return None
        user = matches[0]
        if self.settings['allowed_uids'] and user not in self.settings['allowed_uids']:
            return None
        # Recheck real channel membership after profile lookup / slow inference.
        channel = await raw.get_voice_channel_for_user_strict(user, area=self.actions.area)
        return user if channel == self.actions.voice_channel and self.in_channel() else None

    async def close(self):
        self.music._voice_wake_channel = None
        self.music._voice_wake_standby = None
        self.listeners.clear()
        self.actions.pending.clear()
        self.owner = None
        if self.capture and self.voice:
            try:
                await self.voice.configure_voice_capture(False)
            except Exception:
                LOG.debug('停止语音接收失败', exc_info=True)
        self.capture = False
        if self.asr:
            await self.asr.close()

    async def run(self):
        try:
            while True:
                try:
                    await self.poll()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Do not log audio, transcripts, endpoint tokens or source URLs.
                    LOG.warning('语音控制暂不可用，类型 %s', type(exc).__name__)
                    self.listeners.clear()
                    self.owner = None
                    self.after = time.time() * 1000 + 400
                    await asyncio.sleep(3)
                await asyncio.sleep(.2)
        finally:
            await self.close()

    async def poll(self):
        current_channel = self.music._voice_channel_id
        current_area = self.music._voice_channel_area
        text_channel = getattr(self.music, '_voice_text_channels', {}).get(current_area, '')
        if current_area == self.settings['area']:
            text_channel = text_channel or self.settings['text_channel']
        elif current_area and not text_channel:
            from core.area_config import get_area_registry
            text_channel = get_area_registry().get(current_area).default_channel
        if (current_channel and current_area and text_channel
                and (current_area, current_channel, text_channel) !=
                    (self.actions.area, self.actions.voice_channel, self.actions.text_channel)):
            if self.capture:
                await self.voice.configure_voice_capture(False)
            self.capture = False
            self.listeners.clear()
            self.owner = None
            self.actions.pending.clear()
            self.actions = MusicActions(self.music, self.sender, current_area,
                                        current_channel, text_channel)
            self.generation = None
            self.after = time.time() * 1000
            LOG.info('语音唤醒已跟随机器人当前频道')
        if current_channel and current_area and self.in_channel():
            self.music._voice_wake_channel = (current_area, current_channel)
        if not self.in_channel():
            if self.capture:
                await self.voice.configure_voice_capture(False)
                self.capture = False
                self.listeners.clear()
                self.owner = None
                self.actions.pending.clear()
            if not self.music._voice_channel_id and time.monotonic() >= self.next_join:
                self.next_join = time.monotonic() + 30
                result = await self.music.enter_standby_voice_channel(self.settings['voice_channel'], self.settings['area'])
                self.joined_once = bool(result.get('status'))
                if self.joined_once or result.get('reason') == 'voice_channel_occupied':
                    self.next_join = 0.0
            return
        self.joined_once = True
        if not self.capture:
            result = await self.voice.configure_voice_capture(True)
            if not result.get('ok'):
                raise ValueError('Voice receive setup failed')
            self.capture = True
        data = await self.voice.drain_voice_audio()
        self._received_frames += len(data.get('frames', []))
        if time.monotonic() >= self._diagnostic_at:
            LOG.info('语音唤醒接收状态：音轨数 %s，接收帧数 %s', len(data.get('users', [])), self._received_frames)
            self._received_frames = 0
            self._diagnostic_at = time.monotonic() + 30
        if self.generation != data.get('generation'):
            self.listeners.clear()
            self.actions.pending.clear()
            self.owner = None
            self.generation = data.get('generation')
        users = set(map(str, data.get('users', [])))
        for uid in list(self.listeners):
            if uid not in users:
                self.listeners.pop(uid)
                if self.owner == uid:
                    self.owner = None
        if self.owner and self.owner in self.listeners:
            event = self.listeners[self.owner].tick()
            if event:
                await self.handle(self.owner, event)
        for frame in data.get('frames', [])[:120]:
            uid = str(frame.get('uid', ''))
            if (not uid or frame.get('generation') != self.generation or frame.get('at', 0) <= self.after
                    or (self.owner is not None and uid != self.owner) or uid not in users):
                continue
            if uid not in self.listeners:
                if len(self.listeners) >= 8:
                    continue
                self.listeners[uid] = await asyncio.to_thread(Listener, self.model)
            pcm = base64.b64decode(frame.get('pcm', ''), validate=True)
            event = await asyncio.to_thread(self.listeners[uid].feed, pcm)
            if event:
                await self.handle(uid, event)
                # Drop the remainder of a drained batch after wake or transcription.
                break

    async def handle(self, uid, event):
        kind, pcm = event
        if kind == 'wake':
            LOG.info('检测到唤醒词')
            user = await self.resolve_user(uid)
            if not user:
                LOG.warning('唤醒未通过频道成员身份校验')
                self.listeners[uid].reset()
                return
            self.owner = uid
            tone = await self.voice.play_wake_tone(self.settings.get('wake_volume', 150))
            if not tone.get('ok'):
                LOG.warning('唤醒提示音发布失败')
                self.listeners[uid].reset()
                self.owner = None
                return
            self.after = tone['after']
            self.listeners[uid].ready()
            return
        self.owner = uid
        if kind == 'audio':
            before = (self.music._voice_channel_area, self.music._voice_channel_id, self.music._playback_generation)
            user = await self.resolve_user(uid)
            if user and self.asr:
                text = await self.asr.transcribe(pcm)
                LOG.info('语音指令识别完成：结果非空 %s', bool(text))
                now = (self.music._voice_channel_area, self.music._voice_channel_id, self.music._playback_generation)
                if before == now and await self.resolve_user(uid) == user and self.in_channel():
                    await self.actions.run(text, user)
        self.owner = None
        self.after = time.time() * 1000 + 400
        self.listeners.clear()
