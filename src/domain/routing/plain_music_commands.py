"""Explicit music shortcuts in ordinary channel messages."""

import re


def plain_music_command(content: str) -> str:
    text = content.strip()
    if text in ("停止播放", "下一首"):
        return text
    match = re.fullmatch(r"(播放|b站)\s+(.+)", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    keyword = match[2].strip()
    if not keyword:
        return ""
    prefix = "播放 b站:" if match[1].lower() == "b站" else "播放 "
    return prefix + keyword
