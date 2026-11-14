"""Authenticated local ASR transport; never follows redirects or logs speech."""
import base64
from urllib.parse import urlsplit

import aiohttp


class LocalASR:
    def __init__(self, url, token, hotwords):
        parsed = urlsplit(url)
        if (parsed.scheme not in ('http', 'https') or not parsed.netloc
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/')):
            raise ValueError('识别服务地址应为 http(s)://主机:端口。')
        if not token:
            raise ValueError('请配置本地识别服务的访问密钥。')
        self.url, self.token, self.hotwords = url.rstrip('/'), token, hotwords
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45), trust_env=False)

    async def transcribe(self, pcm):
        if not pcm or len(pcm) > 320000:
            raise ValueError('识别音频长度异常')
        async with self.http.post(self.url + '/transcribe',
                                  headers={'Authorization': 'Bearer ' + self.token},
                                  json={'audio': base64.b64encode(pcm).decode(),
                                        'sample_rate': 16000, 'hotwords': self.hotwords},
                                  allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError(f'本地识别服务请求失败（HTTP {response.status}）。')
            payload = await response.content.read(65537)
            if len(payload) > 65536:
                raise ValueError('识别结果过大')
            import json
            data = json.loads(payload)
            text = data.get('text') if isinstance(data, dict) else None
            if not isinstance(text, str) or len(text) > 500:
                raise ValueError('识别服务返回了无效结果。')
            return text.strip()

    async def close(self):
        await self.http.close()
