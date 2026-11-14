"""Optional GG Bot wake + local ASR + music command integration."""
import asyncio
from pathlib import Path

from core.paths import PROJECT_ROOT
from domain.plugins.base import (
    BotModule,
    PluginConfigField,
    PluginConfigSpec,
    PluginMetadata,
    parse_bool,
    parse_int,
    parse_string_list,
)


class VoiceControl(BotModule):
    def __init__(self):
        self.task = None
        self.service = None

    @property
    def metadata(self):
        return PluginMetadata('voice_control', 'GG Bot 语音唤醒、本地识别与音乐控制（默认关闭）', '0.2.0', 'EmoXW')

    @property
    def config_spec(self):
        return PluginConfigSpec((
            PluginConfigField('enabled', False, cast=parse_bool, description='启用语音唤醒（唤醒模型已内置，需要外部 Nano 识别服务）'),
            PluginConfigField('wake_volume', 150, cast=parse_int,
                              validator=lambda value: 0 <= value <= 200,
                              description='唤醒提示音音量（0~200，默认 150；100 为原始音量），独立于音乐音量'),
            PluginConfigField('area', '', description='待命域 ID；文字点歌后可跟随机器人跨域语音控制'),
            PluginConfigField('voice_channel', '', description='待命语音频道 ID；点歌后跟随机器人当前频道，停止播放后回到此频道'),
            PluginConfigField('text_channel', '', description='点歌通知、选歌确认及帮助消息频道 ID'),
            PluginConfigField('wake_model_path', '', description='留空自动使用插件内置的英文唤醒模型；仅使用其他模型时填写路径'),
            PluginConfigField('asr_url', 'http://host.docker.internal:18767', description='本地 Fun-ASR-Nano 服务地址；原生运行可填 http://127.0.0.1:18767'),
            PluginConfigField('asr_token', '', description='本地识别服务访问密钥，需与服务端一致'),
            PluginConfigField('allowed_uids', [], cast=parse_string_list, description='默认留空：频道内所有人可用，不校验账号身份；填写后仅允许指定 UID'),
            PluginConfigField('hotwords', [], cast=parse_string_list,
                              validator=lambda words: len(words) <= 40 and all(len(w) <= 60 for w in words) and sum(map(len, words)) <= 1000,
                              description='歌手、歌名热词，每行一个，最多 40 个；仅在本地识别服务处理'),
        ))

    async def on_load(self, handler, config=None):
        settings = self.config_spec.apply(config).to_dict() if config is not None else self.config_spec.to_example()
        # Empty optional fields bypass casts in PluginConfigSpec; the form sends ''.
        for key in ('allowed_uids', 'hotwords'):
            settings[key] = parse_string_list(settings.get(key) or [])
        if settings.get('wake_volume') in ('', None):
            settings['wake_volume'] = 150
        if not settings['enabled']:
            return
        for key in ('area', 'voice_channel', 'text_channel'):
            if not str(settings[key]).strip():
                raise ValueError('启用前必须填写域、语音频道和消息频道 ID。')
            settings[key] = str(settings[key]).strip()
        from voice_control.service import VoiceControlService
        configured = str(settings.get('wake_model_path') or '').strip()
        settings['wake_model_path'] = str((Path(PROJECT_ROOT) / configured).resolve() if configured else
                                          Path(__file__).resolve().parent / 'models' / 'vosk-model-small-en-us-0.15')
        self.service = VoiceControlService(handler, settings)
        try:
            await self.service.start()
        except Exception:
            await self.service.close()
            self.service = None
            raise
        self.task = asyncio.create_task(self.service.run(), name='voice-control')

    async def on_config_reload(self, handler, config):
        await self.on_unload()
        await self.on_load(handler, config)

    async def on_unload(self):
        task, self.task = self.task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self.service:
            await self.service.close()
            self.service = None
