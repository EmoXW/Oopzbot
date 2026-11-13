"""Public map passwords, completely separate from authenticated game sessions."""
import asyncio
import json
import re
import time
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import aiohttp

DEFAULT_URL = "https://www.tmini.net/api/sjzmm?type=json"
TZ = ZoneInfo("Asia/Shanghai")
ALIASES = {"大坝": "零号大坝", "溪谷": "长弓溪谷", "航天": "航天基地",
           "监狱": "潮汐监狱", "az3": "AZ3核电站"}


def parse_passwords(payload, today):
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise ValueError("每日密码来源暂时不可用，请稍后重试。")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("每日密码来源返回格式异常。")
    updated = str(data.get("last_updated", ""))
    try:
        updated_day = datetime.fromisoformat(updated).date()
    except ValueError:
        raise ValueError("每日密码来源缺少有效日期，暂不展示。") from None
    match = re.search(r"(\d{1,2})月(\d{1,2})日", str(data.get("update_date", "")))
    if (updated_day != today or not match
            or (int(match[1]), int(match[2])) != (today.month, today.day)):
        raise ValueError("每日密码来源尚未更新到今天，请稍后查询，避免使用过期密码。")
    rows = []
    passwords = data.get("passwords")
    if not isinstance(passwords, list):
        raise ValueError("每日密码来源返回格式异常。")
    for item in passwords[:20]:
        if not isinstance(item, dict):
            continue
        name = item.get("map_name")
        password = item.get("password")
        if isinstance(password, int) and not isinstance(password, bool) and 0 <= password <= 9999:
            password = str(password).zfill(4)
        if (isinstance(name, str) and 0 < len(name) <= 24
                and isinstance(password, str) and re.fullmatch(r"[0-9]{4}", password)):
            rows.append((name.replace("\n", " ").replace("\r", " "), password))
    if not rows:
        raise ValueError("今日暂无可用密码，请稍后重试。")
    return rows


class DailyPasswords:
    def __init__(self, url=DEFAULT_URL):
        parsed = urlsplit(url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.fragment):
            raise ValueError("每日密码地址必须是有效 HTTP(S) 地址，不能包含账号密码。")
        self.url = url
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), trust_env=False,
                                          cookie_jar=aiohttp.DummyCookieJar())
        self.lock = asyncio.Lock()
        self.cache = None
        self.failed_until = 0
        self.last_error = ""

    async def close(self):
        await self.http.close()

    async def get(self, selected=""):
        today = datetime.now(TZ).date()
        selected = ALIASES.get(selected.lower(), selected)
        async with self.lock:
            now = time.monotonic()
            if not self.cache or self.cache[0] != today or now >= self.cache[1]:
                if now < self.failed_until:
                    raise ValueError(self.last_error)
                try:
                    async with self.http.get(self.url, allow_redirects=False) as response:
                        if response.status != 200:
                            raise ValueError(f"每日密码来源暂时不可用（HTTP {response.status}）。")
                        raw = await response.content.read(512 * 1024 + 1)
                        if len(raw) > 512 * 1024:
                            raise ValueError("每日密码来源返回数据过大。")
                        try:
                            payload = json.loads(raw)
                        except (ValueError, UnicodeError):
                            raise ValueError("每日密码来源返回格式异常。") from None
                    rows = parse_passwords(payload, today)
                    self.cache = (today, time.monotonic() + 300, rows)
                except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                    self.last_error = str(exc) if isinstance(exc, ValueError) else "每日密码来源连接失败，请稍后重试。"
                    self.failed_until = time.monotonic() + 60
                    raise ValueError(self.last_error) from None
            rows = self.cache[2]
        if selected:
            rows = [row for row in rows if row[0].lower() == selected.lower()]
            if not rows:
                raise ValueError("未找到该地图，可发送 三角洲 每日密码 查看全部地图。")
        return (f"三角洲每日密码 · {today:%Y-%m-%d}\n"
                + "\n".join(f"{name}：{password}" for name, password in rows))
