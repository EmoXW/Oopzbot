# 三角洲插件

接入 `https://delta-test-api.shallow.ink`，不复用旧三角洲插件的接口、数据库或配置。使用项目镜像已有的 Python 依赖。网页扫码版还需要配套的 Web 路由文件。

## 功能与指令

在频道 @机器人 后发送以下指令，也可以使用 `/df 子命令`：

| 指令 | 用途 |
| --- | --- |
| 三角洲 帮助 | 查看帮助 |
| 三角洲 登录 | 私信获取临时网页链接，打开后用 QQ 扫码 |
| 三角洲 登录 微信 | 私信获取临时网页链接，打开后用微信扫码 |
| 三角洲 取消登录 | 取消当前扫码等待 |
| 三角洲 状态 | 查看本地是否保存登录 |
| 三角洲 信息 | 查询当前账号信息 |
| 三角洲 战绩 烽火 1 | 查询烽火战绩第 1 页；可改为“全面” |
| 三角洲 日报 烽火 | 查询日报；可改为“全面” |
| 三角洲 周报 烽火 | 查询周报；可改为“全面” |
| 三角洲 每日密码 | 无需游戏登录，获取每日密码 |
| 三角洲 退出 | 取消等待登录并清除本地会话；不删除平台绑定 |

这是文字查询初版，复杂数据保留部分后端字段名。暂不包含图片排版、自动推送、多账号切换。每个 Oopz 用户保存最后一次成功绑定的游戏会话，数据位于 `/app/data/delta_force_v2.sqlite3`。私信只发送随机令牌的临时网页链接，网页与后台使用同一端口，无需后台密码。页面自动显示扫码结果，后端绑定和本地保存均成功后才提示成功。二维码不会上传到 Oopz，失败时也不会转发到频道。链接默认 180 秒有效，取消、成功和失败后清除二维码；结果保留 5 分钟，重启失效。持有链接即可查看二维码，请勿转发。

## 配置

参考 `config/plugins/delta_force_v2/example.json`，保存实际配置为同目录 `config.json`：

- `enabled`：设为 `true`。
- `api_key`：新平台创建的 API 密钥。
- `client_id`：新平台个人用户 ID。
- `base_url`：新版后端 HTTPS 地址。
- `login_page_base_url`：机器人 Web 服务可访问的基础地址，例如 `https://music.example.com`，与后台同端口；留空沿用播放器 URL。仅本机使用可填 `http://127.0.0.1:10000`，手机或其他成员访问需填可到达本机的地址，并正确配置代理转发 `/delta-login` 及其子路径。
- `proxy`：默认 `direct`。

密钥只保存在实际配置中，不要提交到公开仓库。登录后的会话数据库同样应按私密配置保管。

## 服务器挂载部署

1. 将插件目录（包括本文件、`__init__.py`、`api.py`、`store.py`、`formatters.py`）上传到 `/opt/apps/oopzbot/botdata/plugin-code/delta_force_v2/`。
2. 将配置上传到 `/opt/apps/oopzbot/botdata/config/plugins/delta_force_v2/config.json`。
3. 在现有 compose 的 `bot.volumes` 中添加：

```yaml
- /opt/apps/oopzbot/botdata/plugin-code/delta_force_v2:/app/plugins/delta_force_v2:ro
```

4. 在 compose 所在目录执行：

```bash
docker compose up -d --no-deps --force-recreate bot
docker compose logs --tail=100 bot
```

当前镜像使用 UID/GID 1000 运行。若以 root 上传配置，需要执行 `chown -R 1000:1000 /opt/apps/oopzbot/botdata/config/plugins/delta_force_v2`，并使实际配置仅对该用户可读写。

如果机器人已有插件启用列表，新目录不会自动加入。请在管理后台的插件页面加载 `delta_force_v2`；加载后会持久化到 `data/plugin_runtime_state.json`，后续重启自动加载。

无需构建或拉取新镜像；内部和外部端口仍为 10000，现有 Redis 和网易云 API 部署保持原配置。后续只更新插件文件时重启 bot 即可。旧版 delta_force 插件必须移除或卸载，避免同名命令冲突。

先发送“三角洲 每日密码”验证 API，再登录扫码，最后发送“三角洲 信息”。如果登录失败，请检查机器人私信权限和新平台的 API 权限。

## 验证范围

已实测新 API 授权、每日密码、QQ 二维码和等待扫码状态。账号信息、战绩、日报、周报和扫码后绑定需真实账号完成扫码后验证；微信扫码也尚未实测。自动测试覆盖状态判断、私信失败不公开二维码、会话隔离持久化、任务卸载和错误信息脱敏。

### 网页扫码版升级

除插件文件外，需同时更新 `src/web/delta_login.py` 和 `src/web/web_player.py`，可重建镜像或只读挂载这三个文件（含插件 `__init__.py`）。仅挂载插件目录到旧镜像无法提供网页。修改挂载后重建 Bot 容器。后台和扫码页面共用已有 Web 服务端口，不新增端口。
