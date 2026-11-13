"""三角洲插件：QQ / 微信登录及 WeGame 数据查询。"""
import asyncio
import contextlib
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import aiosqlite

from core.logger_config import get_logger
from core.paths import DATA_DIR
from domain.plugins.base import (
    BotModule,
    PluginCommandCapabilities,
    PluginConfigField,
    PluginConfigSpec,
    PluginMetadata,
    parse_int,
    validate_range,
)

from .api import APIError, Client, friendly_error
from .daily import DEFAULT_URL, DailyPasswords
from .formatters import role_text
from .web import PREFIX, LoginWeb

LOG = get_logger("DeltaForce")
HELP = """三角洲指令（可直接发送，也可 @机器人）：
三角洲 登录：QQ 扫码登录
三角洲 登录 wx：微信扫码登录
三角洲 状态：查看当前账号登录状态
三角洲 信息：查询角色信息
三角洲 每日密码 [地图]：无需登录，查询今日地图密码
三角洲 统计 <赛季ID> [烽火/全面]：查询赛季统计
三角洲 战绩 [烽火/全面] [1-20]：查询最近战绩
三角洲 解绑：删除当前会话绑定
三角洲 帮助：查看本说明
登录链接和登录结果均通过私信发送。"""
STATES = {"pending": "等待扫码，请使用手机扫码登录。", "scanned": "已扫码，请在手机上确认。",
          "qq_authorized": "QQ 已授权，正在完成 WeGame 登录。",
          "authenticated": "登录成功，账号已绑定。可以返回机器人查询信息和战绩。",
          "expired": "二维码已失效，请重新发送 三角洲 登录。",
          "verification_required": "登录失败：平台要求额外验证，请在官方客户端处理后重新登录。"}
LABELS = {"name": "角色名", "nickname": "昵称", "nick_name": "昵称", "role_name": "角色名",
          "level": "等级", "rank": "段位", "rank_name": "段位", "season_id": "赛季",
          "kills": "击杀", "kill": "击杀", "deaths": "死亡", "assists": "助攻",
          "kd": "KD", "kda": "KDA", "wins": "胜场", "win_rate": "胜率",
          "total_games": "总场次", "games": "场次", "score": "得分", "money": "资产",
          "map_name": "地图", "map": "地图", "start_time": "开始时间", "end_time": "结束时间",
          "duration": "时长", "profit": "收益", "escape_rate": "撤离率", "result": "结果"}


def format_fields(data, depth=0):
    lines = []
    if not isinstance(data, dict) or depth > 2:
        return lines
    for key, value in data.items():
        if any(word in key.lower() for word in ("token", "ticket", "secret", "cookie", "openid", "session", "password", "qr", "uid", "avatar")):
            continue
        if value is None:
            continue
        label = LABELS.get(key, key)
        if isinstance(value, dict):
            lines.extend(f"{label} · {line}" for line in format_fields(value, depth + 1))
        elif isinstance(value, (str, int, float, bool)):
            safe = str(value).replace("\n", " ").replace("\r", " ")[:90]
            lines.append(f"{label}：{safe}")
        if len(lines) >= 24:
            break
    return lines[:24]


def queue_name(value):
    choices = {"烽火": "sol", "烽火地带": "sol", "sol": "sol", "全面": "tdm", "全面战场": "tdm", "tdm": "tdm"}
    if value not in choices:
        raise ValueError("模式请填写 烽火 或 全面")
    return choices[value]


class DeltaForce(BotModule):
    def __init__(self):
        self.pages = {}
        self.tasks = {}
        self.login_lock = asyncio.Lock()
        self.db_lock = asyncio.Lock()
        self.web = None
        self.client = None
        self.daily = None
        self.db = None
        self.host: Any = None
        self.settings = {}

    @property
    def metadata(self):
        return PluginMetadata("delta_force", "三角洲扫码登录、角色、战绩与每日密码", "1.1.1", "EmoXW")

    @property
    def command_capabilities(self):
        if "plain_prefixes" in PluginCommandCapabilities.__dataclass_fields__:
            return PluginCommandCapabilities(mention_prefixes=("三角洲",), plain_prefixes=("三角洲",))
        return PluginCommandCapabilities(mention_prefixes=("三角洲",))

    @property
    def config_spec(self):
        return PluginConfigSpec((
            PluginConfigField("api_base_url", "http://host.docker.internal:8765", description="API 地址，Docker Desktop 访问宿主机用 host.docker.internal"),
            PluginConfigField("api_key", "", description="API 后台生成的 Key"),
            PluginConfigField("api_secret", "", description="API 后台生成的 Secret，仅保存在机器人端"),
            PluginConfigField("daily_password_url", DEFAULT_URL, description="每日密码公开接口地址，不使用游戏 Key/Secret"),
            PluginConfigField("login_timeout", 300, cast=parse_int, validator=validate_range(30, 900), description="扫码等待上限，秒"),
            PluginConfigField("area", 36, cast=parse_int, validator=validate_range(1, 9999), description="游戏大区"),
        ))

    async def on_load(self, handler, config=None):
        from web.web_player import app

        self.host = handler
        self.settings = self.config_spec.apply(config).to_dict() if config is not None else self.config_spec.to_example()
        self.client = Client(self.settings["api_base_url"], self.settings["api_key"], self.settings["api_secret"])
        self.daily = DailyPasswords(self.settings["daily_password_url"])
        root = Path(DATA_DIR) / "delta_force"
        root.mkdir(parents=True, exist_ok=True)
        root.chmod(0o700)
        self.db = await aiosqlite.connect(root / "bindings.sqlite3")
        await self.db.execute("CREATE TABLE IF NOT EXISTS bindings (user TEXT PRIMARY KEY, sid TEXT NOT NULL, provider TEXT NOT NULL)")
        await self.db.commit()
        (root / "bindings.sqlite3").chmod(0o600)
        self.web = LoginWeb(self, app)

    async def on_config_reload(self, handler, config):
        await self.on_unload()
        await self.on_load(handler, config)

    async def on_unload(self):
        if self.web:
            self.web.close()
            self.web = None
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for item in self.pages.values():
            if not item["terminal"]:
                await self.finish(item, "failed", "登录已取消（插件重载），请重新登录。")
                with contextlib.suppress(Exception):
                    assert self.client is not None
                    await self.client.request("DELETE", f"/v1/login/sessions/{quote(item['sid'], safe='')}", item["user"])
        self.tasks.clear()
        self.pages.clear()
        if self.client:
            await self.client.close()
        if self.daily:
            await self.daily.close()
        if self.db:
            await self.db.close()
        self.client = self.db = None
        self.daily = None

    async def binding(self, user):
        assert self.db is not None
        async with self.db_lock, self.db.execute("SELECT sid, provider FROM bindings WHERE user=?", (user,)) as cursor:
            return await cursor.fetchone()

    async def save_binding(self, user, sid, provider):
        assert self.db is not None
        async with self.db_lock:
            await self.db.execute("INSERT OR REPLACE INTO bindings VALUES (?,?,?)", (user, sid, provider))
            await self.db.commit()

    async def forget(self, user):
        assert self.db is not None
        async with self.db_lock:
            await self.db.execute("DELETE FROM bindings WHERE user=?", (user,))
            await self.db.commit()

    async def notify(self, user, text):
        for attempt in range(3):
            try:
                result = await self.host.sender.send_private_message(user, text)
                if isinstance(result, dict) and not result.get("error") and result.get("status") is True:
                    return True
            except Exception:
                pass
            if attempt < 2:
                await asyncio.sleep(1 + attempt)
        LOG.warning("三角洲私信发送失败，用户 %s", user)
        return False

    async def handle_mention(self, text, channel, area, user, handler):
        if text != "三角洲" and not (text.startswith("三角洲") and text[3:4].isspace()):
            return False
        command = text[3:].strip().split()
        try:
            await self.dispatch(command, str(user), channel, area)
        except Exception as exc:
            LOG.warning("三角洲命令失败，类型 %s", type(exc).__name__)
            message = friendly_error(exc)
            if command and command[0] in ("登录", "登陆"):
                await self.notify(str(user), "三角洲登录失败：" + message)
            await self.host.sender.send_message(message, channel=channel, area=area)
        return True

    async def dispatch(self, args, user, channel, area):
        assert self.client is not None
        async def reply(text):
            await self.host.sender.send_message(text, channel=channel, area=area)
        if not user:
            raise ValueError("无法识别发送者，不能操作账号")
        if not args or args == ["帮助"]:
            await reply(HELP)
            return
        action = args[0]
        if action in ("每日密码", "密码"):
            if len(args) > 2:
                raise ValueError("用法：三角洲 每日密码 [地图名]")
            assert self.daily is not None
            await reply(await self.daily.get(args[1] if len(args) == 2 else ""))
            return
        if action in ("登录", "登陆"):
            if len(args) > 2 or (len(args) == 2 and args[1].lower() not in ("qq", "wx", "wechat")):
                raise ValueError("用法：三角洲 登录（QQ）或 三角洲 登录 wx（微信）")
            provider = "wechat" if len(args) == 2 and args[1].lower() in ("wx", "wechat") else "qq"
            await self.login(user, provider)
            await reply("三角洲登录链接已私信发送，请查看机器人私信。")
            return
        binding = await self.binding(user)
        if not binding:
            raise ValueError("请先发送 三角洲 登录，并在私信网页中扫码。")
        sid, provider = binding
        path_sid = quote(sid, safe="")
        if action == "解绑" and len(args) == 1:
            async with self.login_lock:
                task = self.tasks.get(user)
                if task:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                try:
                    await self.client.request("DELETE", f"/v1/login/sessions/{path_sid}", user)
                except APIError as exc:
                    if exc.status not in (404, 410):
                        raise
                await self.forget(user)
            await reply("三角洲账号已解绑，API 会话已删除。")
            return
        if action == "状态" and len(args) == 1:
            data = await self.client.request("GET", f"/v1/login/sessions/{path_sid}", user)
            await reply(("微信" if provider == "wechat" else "QQ") + "：" + STATES.get(data["state"], "状态未知，请重新登录。"))
            return
        params = {"area": self.settings["area"]}
        if action == "信息" and len(args) == 1:
            endpoint, title, field = "role", "三角洲角色信息", "role_info"
        elif action in ("统计", "赛季", "赛季统计") and 2 <= len(args) <= 3:
            if not args[1].isdigit() or not 1 <= int(args[1]) <= 9999:
                raise ValueError("赛季 ID 必须是 1 到 9999 的整数")
            params.update(season=int(args[1]), queue=queue_name(args[2] if len(args) == 3 else "烽火"))
            endpoint, title, field = "report", f"三角洲赛季 {args[1]} 统计", "season"
        elif action == "战绩" and len(args) <= 3:
            mode, size = "sol", 5
            tail = args[1:]
            if tail and not tail[0].isdigit():
                mode = queue_name(tail.pop(0))
            if tail:
                if len(tail) != 1 or not tail[0].isdigit() or not 1 <= int(tail[0]) <= 20:
                    raise ValueError("战绩数量必须为 1 到 20")
                size = int(tail[0])
            params.update(queue=mode, size=size)
            endpoint, title, field = "records", "三角洲最近战绩", "sols" if mode == "sol" else "tdms"
        else:
            await reply(HELP)
            return
        data = await self.client.request("GET", f"/v1/sessions/{path_sid}/{endpoint}?" + urlencode(params), user)
        if data.get("state") == "role_not_found":
            await reply("当前登录渠道和大区未查到三角洲角色，请确认 QQ/微信渠道及插件大区配置。")
            return
        result = data.get("result", {})
        if result.get("error_code", 0) != 0:
            await reply(f"游戏数据查询失败（上游代码 {result.get('error_code')}）。")
            return
        value = data.get(field)
        if endpoint == "role":
            await reply(role_text(value))
            return
        if isinstance(value, list):
            if not value:
                await reply("当前模式暂无战绩记录。")
                return
            for index, record in enumerate(value[:20], 1):
                await reply(f"{title} · 第 {index} 场\n" + "\n".join(format_fields(record)))
        else:
            await reply(title + "\n" + ("\n".join(format_fields(value)) or "当前暂无可展示的数据。"))

    async def login(self, user, provider):
        assert self.client is not None
        from config import WEB_PLAYER_CONFIG

        base = str(WEB_PLAYER_CONFIG.get("url", "")).strip().rstrip("/")
        parsed = urlsplit(base)
        if (parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.query or parsed.fragment
                or parsed.username or parsed.password or parsed.path not in ("", "/")):
            raise ValueError("请先在机器人后台配置有效的播放器 URL，用于生成登录网页链接。")
        async with self.login_lock:
            for token, item in list(self.pages.items()):
                if item["expires"] < time.time():
                    self.pages.pop(token, None)
            previous = self.tasks.get(user)
            if previous:
                previous.cancel()
                await asyncio.gather(previous, return_exceptions=True)
                for item in self.pages.values():
                    if item["user"] == user and not item["terminal"]:
                        await self.finish(item, "failed", "登录已取消（重新发起登录），请使用新链接。")
                        with contextlib.suppress(Exception):
                            await self.client.request("DELETE", f"/v1/login/sessions/{quote(item['sid'], safe='')}", user)
            if len(self.pages) >= 128:
                raise ValueError("当前登录人数较多，请稍后重试。")
            created = await self.client.request("POST", "/v1/login/sessions", user, {"provider": provider})
            sid = created["session_id"]
            try:
                qr, mime = await self.client.request("GET", f"/v1/login/sessions/{quote(sid, safe='')}/qr", user, image=True)
                token = secrets.token_urlsafe(32)
                ttl = max(1, min(int(created["expires_in"]), self.settings["login_timeout"]))
                item = {"sid": sid, "user": user, "provider": provider, "qr": qr, "mime": mime,
                        "state": "pending", "message": STATES["pending"], "terminal": False,
                        "deadline": time.time() + ttl, "expires": time.time() + ttl + 120}
                self.pages[token] = item
                url = base + PREFIX + "/" + token
                if not await self.notify(user, f"三角洲{'微信' if provider == 'wechat' else 'QQ'}登录\n{url}\n链接约 {ttl} 秒内有效，请勿转发。"):
                    self.pages.pop(token, None)
                    raise ValueError("无法发送机器人私信，请检查私信权限后重新登录。")
                self.tasks[user] = asyncio.create_task(self.poll(item, created.get("poll_after_seconds", 2)))
            except BaseException:
                with contextlib.suppress(Exception):
                    await self.client.request("DELETE", f"/v1/login/sessions/{quote(sid, safe='')}", user)
                raise

    async def finish(self, item, state, message):
        item.update(state=state, message=message, terminal=True, qr=b"")
        delivered = await self.notify(item["user"], "三角洲登录：" + message)
        if not delivered:
            item["message"] += "（私信未能送达，请以本页结果为准。）"

    async def poll(self, item, interval):
        assert self.client is not None
        user, sid = item["user"], quote(item["sid"], safe="")
        path = f"/v1/login/sessions/{sid}"
        interval = max(2, int(interval or 2))
        try:
            while time.time() < item["deadline"]:
                await asyncio.sleep(interval)
                if time.time() >= item["deadline"]:
                    break
                try:
                    data = await self.client.request("GET", path, user)
                except APIError as exc:
                    if exc.status in (429, 502, 503):
                        interval = exc.retry_after
                        continue
                    raise
                state = data["state"]
                if state == "authenticated":
                    old = await self.binding(user)
                    await self.save_binding(user, item["sid"], item["provider"])
                    await self.finish(item, state, STATES[state])
                    if old and old[0] != item["sid"]:
                        with contextlib.suppress(Exception):
                            await self.client.request("DELETE", f"/v1/login/sessions/{quote(old[0], safe='')}", user)
                    return
                if state in ("expired", "verification_required"):
                    await self.finish(item, state, STATES[state])
                    return
                if state not in STATES:
                    raise ValueError("API 返回未知登录状态，请重新发起登录。")
                item.update(state=state, message=STATES[state])
                interval = max(2, int(data.get("retry_after") or 2))
            await self.finish(item, "expired", STATES["expired"])
        except asyncio.CancelledError:
            if not item["terminal"]:
                await self.finish(item, "failed", "登录已取消（插件重载、解绑或重新发起登录），请重新登录。")
            raise
        except Exception as exc:
            await self.finish(item, "failed", friendly_error(exc))
        finally:
            if item["state"] != "authenticated":
                with contextlib.suppress(Exception):
                    await self.client.request("DELETE", path, user)
            if self.tasks.get(user) is asyncio.current_task():
                self.tasks.pop(user, None)
