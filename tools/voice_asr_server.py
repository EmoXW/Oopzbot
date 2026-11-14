"""Optional Apple Silicon Fun-ASR-Nano worker. No model downloads at runtime."""
from __future__ import annotations

import argparse
import asyncio
import base64
import functools
import os
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path

from aiohttp import web


class NanoRecognizer:
    def __init__(self, model_path):
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TOKENIZERS_PARALLELISM'] = 'false'
        from mlx_audio.stt.utils import load_model  # pyright: ignore[reportMissingImports]
        if not (Path(model_path) / 'config.json').is_file():
            raise ValueError('请提供已准备好的本地 MLX Fun-ASR-Nano 模型路径。')
        self.model = load_model(str(Path(model_path).resolve()))

    def transcribe(self, audio, hotwords):
        import numpy as np  # pyright: ignore[reportMissingImports]
        waveform = np.frombuffer(audio, dtype='<i2').astype(np.float32) / 32768
        started = time.monotonic()
        result = self.model.generate(waveform, language='auto', hotwords=hotwords, max_tokens=128,
                                     temperature=0, verbose=False)
        return {'text': str(result.text).strip()[:500], 'elapsed': round(time.monotonic() - started, 2)}


def create_app(recognize, token, executor):
    if not token:
        raise ValueError('必须通过 VOICE_ASR_TOKEN 设置识别服务访问密钥。')
    app = web.Application(client_max_size=600000)
    busy = asyncio.Lock()

    async def transcribe(request):
        submitted = request.headers.get('Authorization', '')
        if not secrets.compare_digest(submitted.encode(), ('Bearer ' + token).encode()):
            raise web.HTTPUnauthorized()
        if busy.locked():
            raise web.HTTPServiceUnavailable(text='Recognition busy')
        async with busy:
            try:
                body = await request.json()
                if not isinstance(body, dict) or body.get('sample_rate') != 16000:
                    raise ValueError('Invalid sample rate')
                audio = base64.b64decode(body.get('audio', ''), validate=True)
                words = body.get('hotwords', [])
                if (not audio or len(audio) > 320000 or len(audio) % 2 or not isinstance(words, list)
                        or len(words) > 40 or any(not isinstance(word, str) or len(word) > 60 for word in words)
                        or sum(map(len, words)) > 1000):
                    raise ValueError('Invalid audio / hotwords')
            except (ValueError, TypeError):
                raise web.HTTPBadRequest(text='Invalid audio / hotwords') from None
            try:
                return web.json_response(await asyncio.get_running_loop().run_in_executor(
                    executor, functools.partial(recognize, audio, words)))
            except Exception:
                raise web.HTTPServiceUnavailable(text='Local recognition failed') from None
    app.router.add_post('/transcribe', transcribe)
    return app


async def main(args):
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='local-nano')
    token = os.environ.get('VOICE_ASR_TOKEN', '')
    if not token:
        raise ValueError('请先设置 VOICE_ASR_TOKEN。')
    try:
        model = await asyncio.get_running_loop().run_in_executor(executor, NanoRecognizer, args.model)
        app = create_app(model.transcribe, token, executor)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, args.host, args.port).start()
            print(f'本地识别服务已启动：{args.host}:{args.port}（不保存录音）', flush=True)
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
    finally:
        executor.shutdown(wait=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True, help='已准备好的本地 MLX Fun-ASR-Nano 模型目录')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=18767)
    arguments = parser.parse_args()
    with suppress(KeyboardInterrupt):
        asyncio.run(main(arguments))
