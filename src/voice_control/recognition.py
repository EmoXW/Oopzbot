"""Optional Vosk wake decoder and bounded, single-speaker utterance capture."""
from __future__ import annotations

import json
import math
import re
import time
from array import array
from pathlib import Path
from typing import Any

RATE = 16000
GRAMMAR = ['g g bot', 'gee gee bot', 'gg bot', '[unk]']


class WakeModel:
    def __init__(self, path: str):
        if not (Path(path) / 'am/final.mdl').is_file():
            raise ValueError('唤醒模型不存在，请配置已挂载的英文 Vosk 小模型路径。')
        try:
            from vosk import Model, SetLogLevel  # pyright: ignore[reportMissingImports]
        except ImportError as exc:
            raise ValueError('语音控制需要可选依赖 requirements-voice.txt。') from exc
        SetLogLevel(-1)
        self.model = Model(path)

    def decoder(self):
        from vosk import KaldiRecognizer  # pyright: ignore[reportMissingImports]
        return KaldiRecognizer(self.model, RATE, json.dumps(GRAMMAR))


class Listener:
    def __init__(self, model: Any, clock=time.monotonic):
        self.model, self.clock = model, clock
        self.reset()

    def reset(self):
        # A new decoder prevents trailing partial tokens leaking into the next wake.
        self.decoder = self.model.decoder()
        self.state = 'wake'
        self.deadline = 0.0
        self.pcm = bytearray()
        self.speech = self.silence = 0.0

    def ready(self):
        self.state = 'command'
        self.deadline = self.clock() + 10
        self.pcm.clear()
        self.speech = self.silence = 0.0

    def finish(self):
        audio = bytes(self.pcm) if self.speech >= .2 else b''
        self.reset()
        return ('audio', audio) if audio else ('timeout', b'')

    def tick(self):
        if self.state == 'command' and self.clock() >= self.deadline:
            return self.finish()
        return None

    def feed(self, pcm: bytes):
        if not pcm or len(pcm) > 6400 or len(pcm) % 2:
            raise ValueError('Invalid PCM frame')
        if self.state == 'wake':
            done = self.decoder.AcceptWaveform(pcm)
            result = json.loads(self.decoder.Result() if done else self.decoder.PartialResult())
            phrase = result.get('text', '') if done else result.get('partial', '')
            if re.sub(r'[^a-z]', '', phrase.lower()) in ('ggbot', 'geegeebot'):
                self.state = 'ack'
                return ('wake', b'')
            return None
        if self.state == 'ack':
            return None
        if self.clock() >= self.deadline or len(self.pcm) + len(pcm) > RATE * 20:
            return self.finish()
        self.pcm.extend(pcm)
        samples = array('h', pcm)
        rms = math.sqrt(sum(x * x for x in samples) / len(samples)) / 32768
        duration = len(pcm) / (RATE * 2)
        if rms >= .008:
            self.speech += duration
            self.silence = 0.0
        elif self.speech:
            self.silence += duration
        if self.speech >= .2 and self.silence >= 1.1:
            return self.finish()
        return None
