# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-03-10

### Added
- Initial release of claw-observer
- **Sidecar Service** - Real-time log monitoring for OpenClaw Gateway
  - Log reader supporting file, Docker, journalctl sources
  - Regex-based log parser with versioned rules
  - In-memory state machine (IDLE → THINKING → EXECUTING → REPLYING → ERROR)
  - WebSocket server for real-time state push
- **CLI Commands**
  - `claw-observer serve` - Start the observer service
  - `claw-observer connect` - Connect and view real-time status
  - `claw-observer token` - Generate JWT auth token
- **Terminal UI**
  - Rich-based beautiful terminal rendering
  - Real-time state display with icons and colors
  - Tool execution details panel
- **Security**
  - SSH tunnel support for secure remote access
  - JWT authentication (optional)
- **Deployment**
  - Docker Compose configuration
  - Systemd service file
  - Virtual environment support (uv)
- **Tests**
  - Unit tests for parser rules
  - Unit tests for state machine
  - 27 passing tests

### Technical Details
- Python 3.10+ required
- Dependencies: websockets, rich, typer, paramiko, pyyaml, pyjwt, prometheus-client
- Project structure: sidecar/ (service), cli/ (client)

## [Unreleased]
- Planned: Rule hot-reload
- Planned: Multi-instance support
- Planned: Advanced TUI with Textual
