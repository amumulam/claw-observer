# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OpenClaw Observer monitors OpenClaw Gateway in real-time using a Sidecar + CLI architecture:
- **Sidecar** (`sidecar/`): Reads JSONL logs, parses state transitions, pushes via SSE
- **CLI** (`cli/`): Connects to Sidecar, displays agent states in terminal UI

## Common Commands

```bash
# Install dependencies (uses uv)
uv sync

# Run tests
uv run pytest tests/

# Run single test file
uv run pytest tests/test_state_machine.py -v

# Run with coverage
uv run pytest --cov=sidecar --cov=cli tests/

# Start Sidecar (server) - interactive mode
uv run claw-observer serve

# Start Sidecar with options
uv run claw-observer serve --multi --base-path /root/.openclaw/agents

# Connect CLI client - interactive mode
uv run claw-observer connect

# Connect via SSH tunnel
uv run claw-observer connect --ssh user@server --multi

# Generate JWT token
uv run claw-observer token --secret your-secret
```

## Architecture

### State Machine (5 states)

```
IDLE → THINKING → REPLYING → IDLE
         ↓           ↓
     EXECUTING ←─────┘
         ↓
      ERROR
```

- `IDLE`: Waiting for request
- `THINKING`: LLM processing (user message detected)
- `REPLYING`: LLM streaming response
- `EXECUTING`: Tool execution in progress
- `ERROR`: Tool failure or error log

### Key Modules

| Module | Purpose |
|--------|---------|
| `sidecar/state_machine.py` | State transitions, `StateMachine` and `MultiAgentStateMachine` |
| `sidecar/parser.py` | `LogParser` combines rules + state machine |
| `sidecar/rules/openclaw_v1.py` | JSONL parsing rules for OpenClaw session logs |
| `sidecar/ws_server.py` | SSE server for real-time push |
| `sidecar/log_reader.py` | Log source abstraction (file, docker, journalctl, stdin) |
| `cli/main.py` | Typer CLI with interactive menu |
| `cli/ws_client.py` | SSE client with reconnect logic |
| `cli/ui_renderer.py` | Rich terminal UI |

### Multi-Agent Mode

Each OpenClaw agent has an independent state machine. Log lines have format:
```
{agent_id}\t{jsonl_line}
```

`MultiAgentLogParser` and `MultiAgentStateMachine` track states per agent.

### Log Parsing Rules

Rules in `sidecar/rules/openclaw_v1.py` parse OpenClaw JSONL session logs:
- `OpenClawUserMessageRule`: `role: "user"` → THINKING
- `OpenClawAssistantResponseRule`: `role: "assistant"` + `stopReason: "stop"` → IDLE, else REPLYING
- `OpenClawToolResultRule`: `role: "toolResult"` → EXECUTING or ERROR

## Configuration

Environment variables (see `sidecar/config.py`):
- `WS_HOST` / `WS_PORT`: SSE server bind address (default: `0.0.0.0:8765`)
- `OPENCLAW_MULTI_AGENT`: Enable multi-agent mode
- `OPENCLAW_BASE_PATH`: Path to agents directory (default: `/root/.openclaw/agents`)
- `AUTH_ENABLED` / `JWT_SECRET`: JWT authentication
- `LOG_LEVEL`: Logging level

## SSE Message Format

```json
{
  "type": "state_change",
  "state": "EXECUTING",
  "previous_state": "THINKING",
  "meta": {"tool_name": "browser", "action": "navigate"},
  "raw_log": "..."
}
```

## Testing

Tests use `pytest` with `pytest-asyncio`. Key test files:
- `tests/test_state_machine.py`: State transition tests
- `tests/test_parser_rules.py`: Log parsing rule tests