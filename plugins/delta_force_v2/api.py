"""New Delta backend protocol. Never expose upstream response bodies in errors."""
from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import aiohttp

from core.async_http import ManagedHttpClient


class ApiError(Exception):
    pass


class DeltaApi:
    def __init__(self, config: dict):
        self.config = config
        self.base = str(config["base_url"]).rstrip("/")
        parsed = urlsplit(self.base)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query:
            raise ApiError("API 地址必须是 HTTPS 地址。")
        self.http = ManagedHttpClient(proxy_value=config.get("proxy", "direct"))

    async def close(self):
        await self.http.close()

    async def request(self, path, *, token="", user="", method="GET", body=None, params=None):
        headers = {
            "X-API-Key": self.config["api_key"],
            "X-Client-Type": "bot",
            "X-Client-ID": self.config["client_id"],
            "User-Agent": "oopzbot-delta-v2/0.1.0",
        }
        if token:
            headers["X-Framework-Token"] = token
        if user:
            headers["X-User-Identifier"] = user
        attempts = int(self.config.get("request_retries", 2)) if method == "GET" else 1
        for attempt in range(max(1, attempts)):
            try:
                session = await self.http.session()
                async with session.request(
                    method, self.base + path, headers=headers, params=params, json=body,
                    proxy=self.http.request_proxy(self.base), allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=float(self.config.get("request_timeout_sec", 30))),
                ) as response:
                    if response.status >= 500:
                        raise aiohttp.ClientConnectionError()
                    if response.status in (401, 403):
                        raise ApiError("API 授权失败，请检查密钥、用户 ID 或游戏登录状态。")
                    if not 200 <= response.status < 300:
                        raise ApiError(f"API 请求失败（HTTP {response.status}）。")
                    payload = await response.json(content_type=None)
                if not isinstance(payload, dict) or payload.get("code") not in (0, "0"):
                    raise ApiError("后端未能完成请求；请检查游戏登录状态或稍后重试。")
                return payload.get("data")
            except (aiohttp.ClientError, TimeoutError, ValueError):
                if attempt + 1 >= max(1, attempts):
                    raise ApiError("API 连接失败或响应格式异常，请稍后重试。") from None
                await asyncio.sleep(1)


def login_state(data):
    """Only inner game-login status indicates success, never the outer API code."""
    if not isinstance(data, dict):
        return "pending"
    status = str(data.get("status", "")).lower()
    if status in ("expired", "cancelled", "failed"):
        return "expired"
    if status in ("pending", "scanned", "waiting"):
        return "pending"
    if status in ("success", "confirmed", "logged_in") or data.get("code") in (0, "0"):
        return "success"
    return "pending"
