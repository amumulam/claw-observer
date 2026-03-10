# OpenClaw Observer

实时监控 OpenClaw Gateway 运行状态的 Sidecar 服务 + CLI 终端。

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│                      远端服务器                              │
│  ┌─────────────────┐         ┌───────────────────────────┐  │
│  │ OpenClaw Gateway├────────►│ Sidecar Monitor Service   │  │
│  │                 │ 日志流   │  日志读取 → 解析 → 状态机    │  │
│  │                 │         │  WebSocket Server :8765   │  │
│  └─────────────────┘         └─────────────┬─────────────┘  │
└─────────────────────────────────────────────┼───────────────┘
                                              │ SSH 隧道/TLS
                                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      本地机器                                │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  CLI Terminal UI (Rich)  ← ws://localhost:8765         ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

## 快速开始

### 安装依赖

使用 [uv](https://github.com/astral-sh/uv)：

```bash
# 同步依赖
uv sync

# 同步依赖（包含开发依赖）
uv sync --dev
```

或使用 pip：

```bash
pip install -e .
```

### 启动 Sidecar

```bash
# 方式 1: 使用 uv 运行
uv run python -m sidecar.main

# 方式 2: 安装后运行
uv install
python -m sidecar.main

# 方式 3: 使用 Docker
cd deploy
docker-compose up -d

# 方式 4: 使用 systemd (Linux)
sudo cp deploy/systemd/sidecar-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sidecar-monitor
sudo systemctl start sidecar-monitor
```

### 连接 CLI

```bash
# 使用 uv 运行
uv run claw-observer connect ws://localhost:8765

# 本地连接（安装后）
claw-observer connect ws://localhost:8765

# 通过 SSH 隧道连接远端
claw-observer connect --ssh user@server

# 使用简单模式
claw-observer connect --simple
```

## 状态机

| 状态 | 含义 | 触发条件 |
|------|------|----------|
| `IDLE` | 空闲 | 初始状态 / 任务完成 |
| `THINKING` | LLM 思考中 | 检测到 `dispatching` |
| `REPLYING` | LLM 回复中 | 检测到 `Started streaming` |
| `EXECUTING` | 工具执行中 | 检测到 `[tools] xxx executing` |
| `ERROR` | 错误状态 | 检测到 `ERROR` 或 `[tools] xxx failed` |

## 配置

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `WS_HOST` | WebSocket 监听地址 | `0.0.0.0` |
| `WS_PORT` | WebSocket 端口 | `8765` |
| `JWT_SECRET` | JWT 密钥 | `change-me-in-production` |
| `AUTH_ENABLED` | 启用认证 | `false` |
| `OPENCLAW_LOG_SOURCE` | 日志源 | `auto` |
| `LOG_LEVEL` | 日志级别 | `INFO` |

### 日志源配置

```bash
# 从文件读取
OPENCLAW_LOG_SOURCE=file:/var/log/openclaw/gateway.log

# 从 Docker 容器读取
OPENCLAW_LOG_SOURCE=docker:openclaw-gateway

# 从 journalctl 读取
OPENCLAW_LOG_SOURCE=journalctl:openclaw-gateway

# 从 stdin 读取（管道输入）
OPENCLAW_LOG_SOURCE=stdin
```

## 安全

### 启用 JWT 认证

1. 设置环境变量：
```bash
export JWT_SECRET="your-secret-key"
export AUTH_ENABLED=true
```

2. 生成 token：
```bash
claw-observer token --secret your-secret-key
```

3. 使用 token 连接：
```bash
claw-observer connect ws://server:8765 --token YOUR_TOKEN
```

### SSH 隧道

推荐在生产环境使用 SSH 隧道：

```bash
# 手动建立隧道
ssh -L 8765:localhost:8765 user@server

# CLI 自动建立
claw-observer connect --ssh user@server
```

## API

### WebSocket 消息格式

```json
{
  "type": "state_change",
  "timestamp": "2024-01-15T10:30:00.123Z",
  "instance_id": "openclaw-gateway-1",
  "data": {
    "state": "EXECUTING",
    "previous_state": "THINKING",
    "meta": {
      "tool_name": "browser",
      "action": "navigate"
    }
  }
}
```

### HTTP 端点

| 端点 | 说明 |
|------|------|
| `GET /health` | 健康检查 |
| `GET /metrics` | Prometheus 指标 |

## 测试

```bash
# 运行测试
uv run pytest tests/

# 带覆盖率
uv run pytest --cov=sidecar --cov=cli tests/
```

## 项目结构

```
claw-observer/
├── sidecar/              # Sidecar 服务
│   ├── main.py           # 入口
│   ├── config.py         # 配置
│   ├── log_reader.py     # 日志读取
│   ├── parser.py         # 日志解析
│   ├── state_machine.py  # 状态机
│   ├── ws_server.py      # WebSocket 服务
│   └── rules/            # 解析规则
│       ├── base.py
│       └── openclaw_v1.py
├── cli/                  # CLI 客户端
│   ├── main.py           # CLI 入口
│   ├── ws_client.py      # WebSocket 客户端
│   ├── ui_renderer.py    # 终端 UI
│   ├── tunnel.py         # SSH 隧道
│   └── config.py         # 配置
├── deploy/               # 部署配置
│   ├── Dockerfile.sidecar
│   ├── docker-compose.yml
│   └── systemd/
│       └── sidecar-monitor.service
├── tests/                # 测试
│   ├── test_parser_rules.py
│   ├── test_state_machine.py
│   └── samples/
│       └── openclaw_logs.txt
├── pyproject.toml
├── uv.lock
└── README.md
```

## 开发计划

详见 [开发计划.md](./开发计划.md)

- Phase 1: 核心功能（MVP）- 12 小时
- Phase 2: 工程化 - 10 小时
- Phase 3: 增强功能 - 12 小时

## 许可证

MIT
