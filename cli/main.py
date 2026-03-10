"""
CLI Main Entry Point

Command-line interface for OpenClaw Monitor.
"""

import asyncio
import signal
import sys
from typing import Optional

import typer
from rich.console import Console

from .ws_client import StateClient
from .ui_renderer import StateRenderer, SimpleRenderer
from .tunnel import SSHTunnel
from .config import get_config, CLIConfig

# Import sidecar modules for serve command
from sidecar.main import Sidecar, get_config as get_sidecar_config

app = typer.Typer(
    name="claw-observer",
    help="OpenClaw Gateway Observer CLI",
    add_completion=False,
)
console = Console()

# Global state
_running = True
_state_renderer: Optional[StateRenderer] = None


def signal_handler(sig, frame):
    """Handle Ctrl+C."""
    global _running
    _running = False


@app.command()
def connect(
    uri: Optional[str] = typer.Argument(
        None,
        help="WebSocket URI (e.g., ws://localhost:8765)",
    ),
    ssh: Optional[str] = typer.Option(
        None,
        "--ssh",
        help="SSH host for tunnel (e.g., user@server)",
    ),
    remote_port: int = typer.Option(
        8765,
        "--remote-port",
        help="Remote port",
    ),
    local_port: int = typer.Option(
        8765,
        "--local-port",
        help="Local port for tunnel",
    ),
    token: Optional[str] = typer.Option(
        None,
        "--token",
        help="JWT auth token",
    ),
    simple: bool = typer.Option(
        False,
        "--simple",
        help="Use simple text mode instead of rich UI",
    ),
):
    """
    Connect to OpenClaw Observer Sidecar.

    Examples:

        # Connect to local sidecar
        claw-observer connect ws://localhost:8765

        # Connect via SSH tunnel
        claw-observer connect --ssh user@server

        # Connect with auth token
        claw-observer connect ws://server:8765 --token YOUR_TOKEN
    """
    global _running, _state_renderer

    # Load config
    config = get_config()

    # Determine URI
    ws_uri = uri or config.uri

    # Setup SSH tunnel if requested
    tunnel: Optional[SSHTunnel] = None

    if ssh:
        console.print(f"[cyan]Setting up SSH tunnel to {ssh}...[/cyan]")
        tunnel = SSHTunnel(ssh, remote_port, local_port)

        # Start tunnel
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        success = loop.run_until_complete(tunnel.start())
        if not success:
            console.print("[red]Failed to establish SSH tunnel[/red]")
            sys.exit(1)

        ws_uri = tunnel.local_uri
        console.print(f"[green]SSH tunnel established[/green]")

    # Setup renderer
    if simple:
        _state_renderer = SimpleRenderer()
    else:
        _state_renderer = StateRenderer()

    # Setup client
    auth_token = token or config.auth_token
    client = StateClient(ws_uri, auth_token)

    # Register callbacks
    def on_state_change(previous_state: str, new_state: str, meta: dict):
        if _state_renderer:
            _state_renderer.update_state(new_state, previous_state, meta)

            # Update tool details if available
            if "tool_name" in meta:
                _state_renderer.set_tool_details(
                    meta.get("tool_name", ""),
                    meta.get("action", ""),
                    meta.get("params"),
                )

    def on_connect():
        if _state_renderer:
            _state_renderer.set_connection_status("connected")

    def on_disconnect():
        if _state_renderer:
            _state_renderer.set_connection_status("disconnected")

    client.on_state_change(on_state_change)
    client._client.on_connect(on_connect)
    client._client.on_disconnect(on_disconnect)

    # Setup signal handler
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run the client
    async def run():
        # Start renderer
        if _state_renderer:
            _state_renderer.start()

        try:
            # Connect and run
            await client.connect()
        except Exception as e:
            console.print(f"[red]Connection error: {e}[/red]")
        finally:
            # Cleanup
            if _state_renderer:
                _state_renderer.stop()

            if tunnel:
                await tunnel.stop()

    # Run async
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    finally:
        # Print summary
        stats = client.stats
        console.print(f"\n[yellow]Disconnected[/yellow]")
        console.print(f"Events received: {stats.get('messages_received', 0)}")


@app.command()
def serve(
    log_source: str = typer.Option(
        "auto",
        "--log-source",
        help="Log source: auto, file:/path, docker:container, journalctl:unit",
    ),
    host: str = typer.Option(
        "0.0.0.0",
        "--host",
        help="WebSocket server host",
    ),
    port: int = typer.Option(
        8765,
        "--port",
        help="WebSocket server port",
    ),
):
    """
    Start the Observer service (Sidecar mode).

    Examples:

        # Start with auto-detected log source
        claw-observer serve

        # Start with Docker container logs
        claw-observer serve --log-source docker:openclaw-gateway

        # Start with file logs
        claw-observer serve --log-source file:/var/log/openclaw/gateway.log

        # Custom host and port
        claw-observer serve --host 127.0.0.1 --port 8765
    """
    console.print("[green]Starting OpenClaw Observer service...[/green]")

    # Override config with command-line options
    import os
    os.environ["OPENCLAW_LOG_SOURCE"] = log_source
    os.environ["WS_HOST"] = host
    os.environ["WS_PORT"] = str(port)

    # Import and run sidecar
    from sidecar.main import main as sidecar_main

    try:
        asyncio.run(sidecar_main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Service stopped[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@app.command()
def status(
    uri: Optional[str] = typer.Argument(
        "ws://localhost:8765",
        help="WebSocket URI",
    ),
):
    """
    Check Sidecar status.

    Shows current state and connection info.
    """
    console.print("[yellow]Status command not yet implemented[/yellow]")
    console.print("Use 'claw-observer connect' to see live status")


@app.command()
def token(
    secret: str = typer.Option(
        "change-me-in-production",
        "--secret",
        help="JWT secret key",
    ),
    instance_id: str = typer.Option(
        "openclaw-gateway-1",
        "--instance",
        help="Instance ID",
    ),
    hours: int = typer.Option(
        24,
        "--hours",
        help="Token validity in hours",
    ),
):
    """
    Generate JWT auth token.

    Used for authenticating to Sidecar with auth enabled.
    """
    try:
        import jwt
        from datetime import datetime, timedelta

        payload = {
            "instance_id": instance_id,
            "exp": datetime.utcnow() + timedelta(hours=hours),
            "iat": datetime.utcnow(),
        }

        token = jwt.encode(payload, secret, algorithm="HS256")

        console.print("[green]Generated JWT token:[/green]")
        console.print(f"[cyan]{token}[/cyan]")
        console.print(f"\nValid for [yellow]{hours} hours[/yellow]")
        console.print(f"Use with: [bold]claw-observer connect --token {token[:20]}...[/bold]")

    except ImportError:
        console.print("[red]PyJWT not installed. Install with: pip install pyjwt[/red]")
        sys.exit(1)


def main():
    """Main entry point."""
    app()


if __name__ == "__main__":
    main()
