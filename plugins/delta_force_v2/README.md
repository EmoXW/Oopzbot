# 三角洲插件

接入 `https://delta-test-api.shallow.ink`，不复用旧三角洲插件的接口、数据库或配置。使用项目镜像已有的 Python 依赖，无需重建镜像。

## 功能与指令

在频道 @机器人 后发送以下指令，也可以使用 `/df 子命令`：

| 指令 | 用途 |
| --- | --- |
| 三角洲 帮助 | 查看帮助 |
| 三角洲 登录 | 获取 QQ 登录二维码，只发私信 |
| 三角洲 登录 微信 | 获取微信登录二维码，只发私信 |
| 三角洲 取消登录 | 取消当前扫码等待 |
| 三角洲 状态 | 查看本地是否保存登录 |
| 三角洲 信息 | 查询当前账号信息 |
| 三角洲 战绩 烽火 1 | 查询烽火战绩第 1 页；可改为“全面” |
| 三角洲 日报 烽火 | 查询日报；可改为“全面” |
| 三角洲 周报 烽火 | 查询周报；可改为“全面” |
| 三角洲 每日密码 | 无需游戏登录，获取每日密码 |
| 三角洲 退出 | 取消等待登录并清除本地会话；不删除平台绑定 |

这是文字查询初版，复杂数据保留部分后端字段名。暂不包含图片排版、自动推送、多账号切换。每个 Oopz 用户保存最后一次成功绑定的游戏会话，数据位于 `/app/data/delta_force_v2.sqlite3`。二维码仅私信发送，私信失败不转发到频道；临时文件在登录结束或取消时删除。

## 配置

参考 `config/plugins/delta_force_v2/example.json`，保存实际配置为同目录 `config.json`：

- `enabled`：设为 `true`。
- `api_key`：新平台创建的 API 密钥。
- `client_id`：新平台个人用户 ID。
- `base_url`：新版后端 HTTPS 地址。
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
