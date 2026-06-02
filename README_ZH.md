# Registry Console

<p align="center">
  <img alt="Registry Console dashboard preview" src="docs/images/dashboard.jpg" />
</p>

<p align="center">
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" /></a>
  <img alt="No dependencies" src="https://img.shields.io/badge/Dependencies-zero-087D83?style=for-the-badge" />
  <img alt="Registry API" src="https://img.shields.io/badge/Docker%20Registry-HTTP%20API%20V2-2496ED?style=for-the-badge&logo=docker&logoColor=white" />
  <img alt="Local first" src="https://img.shields.io/badge/Local--first-127.0.0.1-D66B1F?style=for-the-badge" />
  <a href="LICENSE"><img alt="License MIT" src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" /></a>
</p>

<p align="center">
  <a href="README.md">English</a> | <strong>中文</strong>
</p>

一个零第三方依赖的 Python Web 管理台，用于连接兼容 Docker Registry HTTP API V2 的远程镜像仓库。它适合在本机或内网临时管理 Registry：查看镜像、统计 tag、查看 digest/大小/时间，并执行单个或批量删除。

## 预览

### 登录页

![Login view](docs/images/login.jpg)

### 管理台

![Dashboard view](docs/images/dashboard.jpg)

## 特性

- 零第三方 Python 依赖，下载后即可运行。
- 支持匿名仓库、Basic Auth、Bearer token challenge。
- 支持登录时或登录后切换仓库前缀/命名空间。
- 左侧镜像列表异步展示每个镜像下的 tag 数。
- 右侧 tag 列表自动补充大小、digest、时间。
- tag 列表按可读取到的更新时间倒序排列。
- 当前镜像空间按唯一 blob digest 去重估算，多 tag 共享层只计算一次。
- 支持单 tag 删除和批量删除。

## 环境要求

- Python 3.10+
- 运行机器可以访问目标 Docker Registry
- 目标 Registry 兼容 Docker Registry HTTP API V2

不需要安装 Python 第三方依赖。

## 快速启动

```bash
git clone https://github.com/tangguo95/registry-console.git
cd registry-console
python3 app.py
```

默认访问地址：

```text
http://127.0.0.1:8765
```

打开浏览器后填写：

- 仓库地址：例如 `https://registry.example.com`
- 用户名：匿名仓库可留空
- 密码：匿名仓库可留空
- 仓库前缀：可选，例如 `project-a/team-b`

## 启动参数

默认监听本机：

```bash
python3 app.py --host 127.0.0.1 --port 8765
```

如果需要让局域网其他机器访问：

```bash
python3 app.py --host 0.0.0.0 --port 8765
```

然后通过服务器 IP 访问：

```text
http://服务器IP:8765
```

对外开放时建议加反向代理、HTTPS 和访问鉴权。

## 后台运行

使用 `nohup` 简单后台运行：

```bash
nohup python3 app.py --host 127.0.0.1 --port 8765 > docker_remote_manage.log 2>&1 &
```

查看进程：

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN
```

停止服务：

```bash
kill <PID>
```

macOS 可以用 `launchctl` 临时托管：

```bash
launchctl submit -l registry_console \
  -o /tmp/registry_console.log \
  -e /tmp/registry_console.log \
  -- /bin/zsh -lc 'cd /path/to/registry-console && python3 app.py --host 127.0.0.1 --port 8765'
```

停止：

```bash
launchctl remove registry_console
```

## 使用说明

1. 打开 `http://127.0.0.1:8765`。
2. 输入 Registry 地址、用户名、密码。
3. 可选填写仓库前缀，限制只展示某个项目/命名空间下的镜像。
4. 登录后从左侧选择镜像。
5. 右侧查看 tag、大小、digest、时间。
6. 勾选多个 tag 后可批量删除。
7. 顶部“当前镜像空间”会估算当前镜像的唯一 blob 占用。

## 项目结构

```text
registry-console/
├── app.py
├── README.md
├── README_ZH.md
├── docs/
│   └── images/
│       ├── dashboard.jpg
│       └── login.jpg
└── static/
    ├── app.js
    ├── index.html
    └── styles.css
```

`app.py` 是后端 HTTP 服务和 Registry API 调用逻辑，`static/` 是前端页面，`docs/images/` 是 README 预览图。

## API 覆盖

当前主要使用这些 Registry V2 API：

- `GET /v2/`
- `GET /v2/_catalog`
- `GET /v2/<name>/tags/list`
- `GET /v2/<name>/manifests/<reference>`
- `GET /v2/<name>/blobs/<digest>`
- `DELETE /v2/<name>/manifests/<digest>`

## 常见问题

### 端口被占用

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN
python3 app.py --port 8766
```

### 服务重启后为什么需要重新登录

登录凭据只保存在当前 Python 进程的内存 session 中。服务重启后需要重新登录。

### 密码里有特殊符号怎么办

页面输入密码不需要额外转义。

如果用 Docker CLI 测试，密码包含 `!`、`@`、`%` 等字符时，建议使用：

```bash
printf '%s' '你的密码' | docker login registry.example.com -u '用户名' --password-stdin
```

不要直接把带特殊符号的密码裸写在 `-p` 后面，zsh 可能会做历史展开。

### 删除后空间为什么没有马上释放

Docker Registry 删除 manifest 后，通常还需要仓库侧执行 garbage collection 才会真正释放存储空间。

### 空间统计和真实磁盘占用为什么不完全一致

空间统计基于 Registry V2 API 可读取到的 manifest/config/layers descriptor 估算，不等同于仓库后端文件系统实际占用。Registry V2 标准接口也不提供服务器总容量/剩余容量。

### 上传时间为什么不一定准确

标准 Registry V2 API 通常不提供准确的推送时间。本工具优先显示 manifest 响应头 `Last-Modified`，否则读取 image config 的 `created` 作为参考时间。

## 安全建议

- 默认建议只监听 `127.0.0.1`。
- 如果给多人使用，建议放在内网，并增加 HTTPS、用户鉴权、审计日志和 CSRF 防护。
- 登录凭据只保存在当前 Python 进程内存中，不会写入本地文件。

## AI 辅助开发

本项目在作者主导与审阅下，借助 OpenAI Codex（GPT-5.5）完成设计、实现、文档编写与细节打磨。

## 联系方式

- Author: tangguo95
- Email: 545496535@qq.com

## 开源协议

本项目基于 [MIT License](LICENSE) 开源。

## 参考

- Docker Distribution / Registry HTTP API V2: https://distribution.github.io/distribution/spec/api/
- Docker Registry authentication: https://docs.docker.com/reference/api/registry/auth/
