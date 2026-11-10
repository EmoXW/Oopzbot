from __future__ import annotations

import asyncio
import base64
import contextlib
from urllib.parse import urlsplit

from domain.plugins.base import (
    BotModule,
    PluginCommandCapabilities,
    PluginConfigField,
    PluginConfigSpec,
    PluginMetadata,
    parse_int,
    validate_range,
)
from plugins._shared.command_mixin import PluginCommandMixin
from web.delta_login import create_ticket, finish_ticket

from .api import ApiError, DeltaApi, login_state
from .formatters import render
from .store import SessionStore

HELP = """三角洲（新后端，文字查询版）
@机器人 三角洲 登录 [qq/微信]
@机器人 三角洲 状态 / 退出 / 取消登录
@机器人 三角洲 信息
@机器人 三角洲 战绩 [烽火/全面] [页码]
@机器人 三角洲 日报 [烽火/全面]
@机器人 三角洲 周报 [烽火/全面]
@机器人 三角洲 每日密码
也可使用 /df 登录、/df 信息 等。
登录页面链接仅私信发送，打开网页扫码。退出只清除本机器人本地登录记录。
暂不支持旧版图片渲染、定时推送和多账号切换。"""


class DeltaForceV2Plugin(PluginCommandMixin, BotModule):
    def __init__(self):
        self.api = None
        self.config = {}
        self.store = SessionStore()
        self.tasks = {}

    @property
    def metadata(self):
        return PluginMetadata("delta_force_v2", "三角洲后端：扫码登录与文字战绩", "0.1.0")

    @property
    def command_capabilities(self):
        return PluginCommandCapabilities(("三角洲",), ("/df",), True)

    @property
    def private_modules(self):
        return tuple(f"plugins.delta_force_v2.{name}" for name in ("api", "store", "formatters"))

    @property
    def config_spec(self):
        return PluginConfigSpec((
            PluginConfigField("enabled", default=False),
            PluginConfigField("api_key", default="", description="新后端 API 密钥"),
            PluginConfigField("client_id", default="", description="新后端平台个人用户 ID"),
            PluginConfigField("base_url", default="https://delta-test-api.shallow.ink"),
            PluginConfigField("login_page_base_url", default="", description="可访问的机器人网页基础地址，留空使用播放器 URL"),
            PluginConfigField("request_timeout_sec", default=30, cast=parse_int, validator=validate_range(5, 120)),
            PluginConfigField("request_retries", default=2, cast=parse_int, validator=validate_range(1, 5)),
            PluginConfigField("login_timeout_sec", default=180, cast=parse_int, validator=validate_range(30, 300)),
            PluginConfigField("login_poll_interval_sec", default=3, cast=parse_int, validator=validate_range(1, 15)),
            PluginConfigField("proxy", default="direct"),
        ))

    async def on_load(self, handler, config=None):
        await self.on_unload()
        self.config = self.config_spec.to_example()
        if config is not None:
            self.config.update(dict(config))
        if self.config.get("enabled") and self.config.get("api_key") and self.config.get("client_id"):
            self.api = DeltaApi(self.config)

    async def on_config_reload(self, handler, config):
        await self.on_load(handler, config)

    async def on_unload(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        if self.api:
            await self.api.close()
            self.api = None

    async def dispatch_command(self, command_text, channel, area, user, handler):
        # Catch all failures here so the shared mixin cannot echo upstream secrets.
        try:
            reply = await self._command(command_text, str(user), handler)
        except ApiError as exc:
            reply = str(exc)
        except Exception:
            reply = "三角洲处理失败，请检查配置或稍后重试。"
        await self._send(handler, reply, channel, area)

    async def _command(self, text, user, handler):
        args = text.strip().split()
        command = args[0] if args else "帮助"
        command = {"登陆": "登录", "取消登陆": "取消登录"}.get(command, command)
        if command in ("帮助", "help"):
            return HELP
        if not self.api:
            return "三角洲未启用或缺少 API 配置，请在管理后台配置 delta_force_v2。"
        client = str(self.config["client_id"])
        if command == "取消登录":
            task = self.tasks.get(user)
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            return "已取消等待登录。"
        if command == "登录":
            if user in self.tasks:
                return "已有登录请求，请查看私信，或先取消登录。"
            platform = "wechat" if len(args) > 1 and args[1] in ("微信", "wechat") else "qq"
            self.tasks[user] = asyncio.create_task(self._login(user, platform, handler))
            return "正在创建扫码登录页面，请查看机器人私信中的临时链接。"
        if command == "退出":
            if user in self.tasks:
                self.tasks[user].cancel()
                await asyncio.gather(self.tasks[user], return_exceptions=True)
            await self.store.delete(client, user)
            return "已清除本机器人的本地游戏登录记录。后端绑定可在新版平台管理。"
        if command in ("每日密码", "密码"):
            data = await self.api.request("/api/v1/df/tools/dailykeyword")
            items = data.get("list", []) if isinstance(data, dict) else []
            return "每日密码\n" + ("\n".join(
                f"{item.get('mapName', '地图')}：{item.get('secret', '暂无')}"
                for item in items if isinstance(item, dict)
            ) or "暂无数据")
        token = await self.store.get(client, user)
        if command in ("状态", "账号"):
            return "本地已保存游戏登录，请使用“信息”验证有效性。" if token else "尚未登录，请发送“三角洲 登录”。"
        if not token:
            return "请先发送“三角洲 登录”并扫码。"
        mode = "mp" if len(args) > 1 and args[1] in ("全面", "mp") else "sol"
        routes = {
            "信息": ("personalinfo", {"seasonid": "0"}),
            "战绩": ("record", {"type": mode, "page": "1", "enrich": "true"}),
            "日报": ("dailyrecord", {"type": mode}),
            "周报": ("weeklyrecord", {"type": mode}),
        }
        if command not in routes:
            return HELP
        endpoint, params = routes[command]
        if command == "战绩" and len(args) > 2:
            if not args[2].isdigit() or not 1 <= int(args[2]) <= 100:
                return "战绩页码请填写 1 至 100。"
            params["page"] = args[2]
        data = await self.api.request(f"/api/v1/df/person/{endpoint}", token=token, params=params)
        return render(f"三角洲{command}", data)

    async def _login(self, user, platform, handler):
        ticket = ""
        try:
            from web.web_player_config import WEB_PLAYER_CONFIG

            base = str(self.config.get("login_page_base_url") or WEB_PLAYER_CONFIG.get("url") or "").rstrip("/")
            parsed = urlsplit(base)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
                raise ApiError("请在三角洲配置中填写可访问的 login_page_base_url（后台同端口基础地址）。")
            api = self.api
            if api is None:
                return
            data = await api.request(f"/api/v1/login/{platform}/qr")
            if not isinstance(data, dict):
                raise ApiError("二维码响应异常。")
            token = data.get("frameworkToken")
            image = str(data.get("qr_image", ""))
            if not token or len(image) > 4_000_000:
                raise ApiError("二维码响应异常。")
            if image.startswith("data:image/") and ";base64," in image:
                image = image.split(";base64,", 1)[1]
            try:
                raw = base64.b64decode(image, validate=True)
            except ValueError:
                raise ApiError("后端二维码格式不受支持。") from None
            if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ApiError("后端未返回 PNG 二维码。")
            ticket = create_ticket(base64.b64encode(raw).decode("ascii"), int(self.config.get("login_timeout_sec", 180)))
            result = await handler.sender.send_private_message(
                user, f"三角洲临时扫码登录页面：{base}/delta-login#{ticket}\n请使用对应客户端扫码；此链接仅限本人使用，请勿转发。",
            )
            if not isinstance(result, dict) or result.get("error"):
                raise ApiError("登录链接私信发送失败，请检查私信权限后重新登录。")
            deadline = asyncio.get_running_loop().time() + int(self.config.get("login_timeout_sec", 180))
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(int(self.config.get("login_poll_interval_sec", 3)))
                status = await api.request(f"/api/v1/login/{platform}/status", token=token)
                state = login_state(status)
                if state == "expired":
                    raise ApiError("二维码已失效，请重新登录。")
                if state != "success" or not isinstance(status, dict):
                    continue
                final_token = status.get("frameworkToken") or status.get("token") or token
                await api.request("/api/v1/user/bindings", method="POST", user=user, body={
                    "framework_token": final_token, "user_identifier": user,
                    "client_type": "bot", "client_id": self.config["client_id"],
                })
                await self.store.put(str(self.config["client_id"]), user, final_token)
                finish_ticket(ticket, "success", "登录并绑定成功！现在可以回到 Oopz 使用“三角洲 信息”。")
                with contextlib.suppress(Exception):
                    await handler.sender.send_private_message(user, "三角洲登录并绑定成功，可以使用三角洲 信息。")
                return
            raise ApiError("等待扫码超时，请重新登录。")
        except asyncio.CancelledError:
            if ticket:
                finish_ticket(ticket, "cancelled", "登录已取消，请重新发送“三角洲 登录”。")
            raise
        except ApiError as exc:
            if ticket:
                finish_ticket(ticket, "error", str(exc))
            with contextlib.suppress(Exception):
                await handler.sender.send_private_message(user, str(exc))
        except Exception:
            if ticket:
                finish_ticket(ticket, "error", "登录处理失败，请重新发送“三角洲 登录”。")
            with contextlib.suppress(Exception):
                await handler.sender.send_private_message(user, "登录处理失败，请稍后重试或检查私信权限。")
        finally:
            self.tasks.pop(user, None)
