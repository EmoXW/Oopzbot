"""Async client for the locally documented WeGame HMAC API."""
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import urlsplit

import aiohttp


class APIError(Exception):
    def __init__(self, status, code="", retry_after=2):
        self.status = status
        self.code = code
        self.retry_after = max(2, min(60, int(retry_after or 2)))
        super().__init__(f"API HTTP {status} ({code})")


def sign_headers(key, secret, user, method, path, body=b"", timestamp=None, nonce=None):
    timestamp = str(int(time.time())) if timestamp is None else str(timestamp)
    nonce = nonce or secrets.token_hex(16)
    canonical = "\n".join((method.upper(), path, timestamp, nonce, user, hashlib.sha256(body).hexdigest()))
    return {
        "X-API-Key": key, "X-User-ID": user, "X-Timestamp": timestamp, "X-Nonce": nonce,
        "X-Signature": hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest(),
    }


class Client:
    def __init__(self, base_url, key, secret):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in ("http", "https") or not parsed.netloc
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            raise ValueError("API 地址必须为 http(s)://主机:端口，不包含路径或账号")
        self.url, self.key, self.secret = base_url.rstrip("/"), key, secret
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90), trust_env=False)

    async def close(self):
        await self.http.close()

    async def request(self, method, path, user, data=None, image=False) -> Any:
        if not self.key or not self.secret:
            raise ValueError("请先在插件配置中填写 api_key 和 api_secret")
        body = b"" if data is None else json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        headers = sign_headers(self.key, self.secret, user, method, path, body)
        if data is not None:
            headers["Content-Type"] = "application/json"
        async with self.http.request(method, self.url + path, data=body if data is not None else None,
                                     headers=headers, allow_redirects=False) as response:
            if response.status >= 300:
                try:
                    error = await response.json()
                    code = error.get("error", {}).get("code", "UNKNOWN")
                except (ValueError, aiohttp.ContentTypeError):
                    code = "UNKNOWN"
                retry = response.headers.get("Retry-After", "2")
                raise APIError(response.status, code, int(retry) if retry.isdigit() else 2)
            if image:
                if response.content_type not in ("image/png", "image/jpeg"):
                    raise ValueError("API 未返回有效的二维码图片")
                payload = await response.content.read(2 * 1024 * 1024 + 1)
                if len(payload) > 2 * 1024 * 1024:
                    raise ValueError("二维码图片过大")
                return payload, response.content_type
            if response.status == 204:
                return {}
            return await response.json()


def friendly_error(exc):
    if isinstance(exc, APIError):
        if exc.status == 401:
            return "API 鉴权失败，请管理员检查 Key、Secret 和系统时间。"
        if exc.status in (404, 410):
            return "登录会话不存在或已过期，请重新发送 三角洲 登录。"
        if exc.status == 429:
            return "API 请求过于频繁，请稍后重试。"
        if exc.status == 409:
            return "登录尚未完成或会话正忙，请稍后再试。"
        return f"API 请求失败（HTTP {exc.status}，{exc.code}）。"
    if isinstance(exc, (aiohttp.ClientError, TimeoutError)):
        return "无法连接三角洲 API 或请求超时，请稍后重试。"
    if isinstance(exc, ValueError):
        return str(exc)
    return "操作失败，请联系管理员查看插件日志。"
