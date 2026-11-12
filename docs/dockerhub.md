# Docker Hub 镜像

镜像地址：[`yuan180/oopzbot`](https://hub.docker.com/r/yuan180/oopzbot)。
平台为 `linux/amd64` 和 `linux/arm64`（多架构标签，Docker 自动选择本机架构），包含 Chromium 和 Python 运行依赖，使用非 root 用户运行。
镜像保留网易云和 B 站功能，移除了屏幕共享与 QQ 音乐，并包含后台登录状态修复。

## 使用已发布镜像

先按[快速开始的 Docker 部署章节](quickstart.md#5-docker-部署)准备
`.env`、`config/runtime.py`、`config/private_key.py`、数据/日志目录和 Nginx 证书。
账号、Cookie、私钥和插件运行配置通过本地挂载提供，不包含在镜像中。

在 `.env` 中添加：

```dotenv
BOT_IMAGE=yuan180/oopzbot:latest
```

然后在项目目录执行：

```shell
docker compose pull
docker compose up -d --no-build
```

Compose 会启动 Bot、Redis、网易云 API 和 Nginx。
网易云 API 是独立容器，由 Compose 自动启动，不在 Bot 镜像内重复安装。
保留 `config`、`data`、`logs` 挂载目录及 Redis 数据卷即可在更新容器时保留相应数据。
配置目录及数据目录的宿主 UID/GID 需与 `BOT_UID`、`BOT_GID` 一致，详见快速开始。

## 镜像标签与回滚

- `latest`：最近一次显式发布的多架构镜像。
- `sha-<12 位提交号>`：对应源码提交的固定版本。

需要回滚时，把 `.env` 中的 `BOT_IMAGE` 改成对应固定版本，再执行上述拉取、启动命令。

## 发布新版本

仓库 Secret `DOCKERHUB_TOKEN` 存放 Docker Hub 的 Read & Write 访问令牌，
令牌所属账号应为工作流中的 `DOCKERHUB_USERNAME`（当前为 `yuan180`）。

进入 GitHub Actions → Docker Build → Run workflow，勾选 `publish`。
工作流分别在原生 AMD64 与 ARM64 runner 上验证 Compose/Nginx、构建镜像、测试非 root Chromium 和挂载目录权限。
各架构验证后先推送独立的临时构建标签；只有两边全部成功才合并发布 `latest` 和提交号多架构标签，结果写入运行摘要。
普通 push 和 pull request 只验证构建，不发布镜像。也可以在推送 main 的提交信息中显式添加 `[publish docker]` 触发发布。

Apple 芯片 Mac 使用这些新标签时无需指定 `platform: linux/amd64`；如旧 Compose 强制指定了该平台，请移除此项才能选择 ARM64 原生镜像。旧的单架构固定版本标签保持不变。

## 镜像体积

镜像仅安装完整 Chromium（Playwright `--no-shell`），语音、图片渲染使用 `chromium` 通道，浏览器登录通过现有 `CHROME_BIN` 配置使用相同的完整 Chromium，不再额外安装 Headless Shell。浏览器依赖安装后在同一构建层清理 apt 索引；保留 Selenium 备用路径、字体与运行依赖。两个架构均验证普通页面及持久化登录上下文启动。
