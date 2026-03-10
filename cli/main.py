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

# Get version from package metadata (must be before app definition)
try:
    from importlib.metadata import version
    __version__ = version("claw-observer")
except Exception:
    __version__ = "0.1.0"

from .ws_client import StateClient, MultiAgentStateClient
from .ui_renderer import StateRenderer, SimpleRenderer, MultiAgentStateRenderer
from .tunnel import SSHTunnel
from .config import get_config, CLIConfig

# Import sidecar modules for serve command
from sidecar.main import Sidecar, get_config as get_sidecar_config

app = typer.Typer(
    name="claw-observer",
    help=f"OpenClaw Gateway Observer CLI v{__version__}",
    add_completion=False,
)
console = Console()

# Global state
_running = True
_state_renderer: Optional[StateRenderer] = None


@app.command()
def menu():
    """
    OpenClaw Observer - Main Menu

    Interactive menu to select and run commands.
    """
    console.print("\n[bold]╔════════════════════════════════════════╗[/bold]")
    console.print("[bold]║   OpenClaw Gateway Observer v{version}   ║[/bold]".format(version=__version__))
    console.print("[bold]╚════════════════════════════════════════╝[/bold]\n")

    while True:
        console.print("\n[bold]Main Menu:[/bold]")
        console.print("  1. Serve - Start Observer service (server)")
        console.print("  2. Connect - Connect to Observer (client)")
        console.print("  3. Token - Generate JWT auth token")
        console.print("  4. Status - Check service status")
        console.print("  5. Exit")

        choice = console.input("\nSelect option [1-5]: ").strip()

        if choice == "1":
            console.print("\n[cyan]Starting Serve setup...[/cyan]")
            config = _interactive_serve_setup()
            _run_serve(config)
        elif choice == "2":
            console.print("\n[cyan]Starting Connect setup...[/cyan]")
            config = _interactive_connect_setup()
            _run_connect(config)
        elif choice == "3":
            _run_token()
        elif choice == "4":
            _run_status()
        elif choice == "5" or choice.lower() in ("q", "quit", "exit"):
            console.print("\n[yellow]Goodbye![/yellow]\n")
            break
        else:
            console.print("[red]Invalid choice, please try again[/red]")


def _run_serve(config: dict) -> None:
    """Run serve command with config."""
    mode_str = "multi-agent" if config["multi"] else "single-agent"
    console.print(f"\n[green]Starting OpenClaw Observer service v{__version__}...[/green]")
    console.print(f"  Mode: [cyan]{mode_str}[/cyan]")
    if config["multi"]:
        console.print(f"  Base path: [cyan]{config['base_path']}[/cyan]")
        if config.get("agents"):
            console.print(f"  Agents: [cyan]{config['agents']}[/cyan]")
        else:
            console.print(f"  Agents: [cyan]auto-discover[/cyan]")
    else:
        console.print(f"  Log source: [cyan]{config['log_source']}[/cyan]")
    console.print(f"  WebSocket: [cyan]{config['host']}:{config['port']}[/cyan]")
    console.print("[bold green]✓ Service starting...[/bold green]\n")

    import os
    os.environ["WS_HOST"] = str(config["host"])
    os.environ["WS_PORT"] = str(config["port"])

    if config["multi"]:
        os.environ["OPENCLAW_MULTI_AGENT"] = "true"
        os.environ["OPENCLAW_BASE_PATH"] = config["base_path"]
        if config.get("agents"):
            os.environ["OPENCLAW_AGENT_IDS"] = config["agents"]
    else:
        os.environ["OPENCLAW_LOG_SOURCE"] = config["log_source"]

    from sidecar.main import main as sidecar_main

    try:
        asyncio.run(sidecar_main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Service stopped[/yellow]")
    except Exception as e:
        console.print(f"\n[bold red]✗ Error: {e}[/bold red]")
        sys.exit(1)


def _run_connect(config: dict) -> None:
    """Run connect command with config."""
    global _running, _state_renderer

    ws_uri = config["uri"]

    # Setup SSH tunnel if requested
    tunnel: Optional[SSHTunnel] = None

    if config["ssh"]:
        console.print(f"\n[cyan]Setting up SSH tunnel to {config['ssh']}...[/cyan]")
        tunnel = SSHTunnel(
            config["ssh"],
            config["remote_port"],
            config["local_port"],
        )

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        success = loop.run_until_complete(tunnel.start())
        if not success:
            console.print("[red]Failed to establish SSH tunnel[/red]")
            return

        ws_uri = tunnel.local_uri
        console.print(f"[green]SSH tunnel established[/green]")

    # Setup renderer and client
    if config["multi"]:
        _state_renderer = MultiAgentStateRenderer()
        client = MultiAgentStateClient(ws_uri, config["token"])

        def on_state_change(agent_id: str, previous_state: str, new_state: str, meta: dict):
            if _state_renderer:
                _state_renderer.update_state(agent_id, new_state, previous_state, meta)

        def on_connect():
            if _state_renderer:
                _state_renderer.set_connection_status("connected")

        def on_disconnect():
            if _state_renderer:
                _state_renderer.set_connection_status("disconnected")

        client.on_state_change(on_state_change)
        client._client.on_connect(on_connect)
        client._client.on_disconnect(on_disconnect)
    else:
        _state_renderer = SimpleRenderer() if config["simple"] else StateRenderer()
        client = StateClient(ws_uri, config["token"])

        def on_state_change_single(previous_state: str, new_state: str, meta: dict):
            if _state_renderer:
                _state_renderer.update_state(new_state, previous_state, meta)
                if "tool_name" in meta:
                    _state_renderer.set_tool_details(
                        meta.get("tool_name", ""),
                        meta.get("action", ""),
                        meta.get("params"),
                    )

        def on_connect_single():
            if _state_renderer:
                _state_renderer.set_connection_status("connected")

        def on_disconnect_single():
            if _state_renderer:
                _state_renderer.set_connection_status("disconnected")

        client.on_state_change(on_state_change_single)
        client._client.on_connect(on_connect_single)
        client._client.on_disconnect(on_disconnect_single)

    # Setup signal handler
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    async def run():
        if _state_renderer:
            _state_renderer.start()

        try:
            await client.connect()
        except Exception as e:
            console.print(f"[red]Connection error: {e}[/red]")
        finally:
            if _state_renderer:
                _state_renderer.stop()
            if tunnel:
                await tunnel.stop()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    finally:
        stats = client.stats
        console.print(f"\n[yellow]Disconnected[/yellow]")
        console.print(f"Events received: {stats.get('messages_received', 0)}")


def _run_token() -> None:
    """Run token command."""
    console.print("\n[bold]Generate JWT Auth Token[/bold]\n")

    secret = _get_str_input(
        "Enter JWT secret key",
        default="change-me-in-production",
    )
    instance_id = _get_str_input(
        "Enter instance ID",
        default="openclaw-gateway-1",
    )
    hours = int(_get_str_input("Token validity (hours)", default="24"))

    try:
        import jwt
        from datetime import datetime, timedelta

        payload = {
            "instance_id": instance_id,
            "exp": datetime.utcnow() + timedelta(hours=hours),
            "iat": datetime.utcnow(),
        }

        token = jwt.encode(payload, secret, algorithm="HS256")

        console.print("\n[green]Generated JWT token:[/green]")
        console.print(f"[cyan]{token}[/cyan]")
        console.print(f"\nValid for [yellow]{hours} hours[/yellow]")
        console.print(f"Use with: [bold]claw-observer connect --token {token[:20]}...[/bold]\n")

    except ImportError:
        console.print("[red]PyJWT not installed. Install with: pip install pyjwt[/red]")


def _run_status() -> None:
    """Run status command."""
    console.print("\n[yellow]Status command - checking local service...[/yellow]")
    console.print("Use 'claw-observer connect' to see live status\n")


def _prompt_for_option(prompt: str, options: list[str], default: str = None) -> str:
    """Prompt user to select an option."""
    console.print(f"\n[bold]{prompt}[/bold]")
    for i, option in enumerate(options, 1):
        marker = " (default)" if option == default else ""
        console.print(f"  {i}. {option}{marker}")

    while True:
        choice = console.input(f"\nEnter choice [1-{len(options)}] (default: {default or 1}): ").strip()
        if not choice and default:
            return default
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except ValueError:
            pass
        console.print("[red]Invalid choice, please try again[/red]")


def _prompt_yes_no(prompt: str, default: bool = True) -> bool:
    """Prompt user for yes/no."""
    default_str = "[Y/n]" if default else "[y/N]"
    response = console.input(f"{prompt} {default_str}: ").strip().lower()
    if not response:
        return default
    return response in ("y", "yes")


def _get_str_input(prompt: str, default: str = None, allow_empty: bool = False) -> str:
    """Get string input from user."""
    default_str = f" (default: {default})" if default else ""
    while True:
        response = console.input(f"{prompt}{default_str}: ").strip()
        if not response:
            if default:
                return default
            if allow_empty:
                return ""
            console.print("[red]This field is required, please enter a value[/red]")
            continue
        return response


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
    multi: Optional[bool] = typer.Option(
        None,
        "--multi",
        help="Use multi-agent mode to display multiple agents",
    ),
    interactive: bool = typer.Option(
        False,
        "-i",
        "--interactive",
        help="Interactive mode with guided setup",
    ),
):
    """
    Connect to OpenClaw Observer Sidecar.

    Run without arguments for interactive setup.

    Examples:

        # Interactive mode (guided setup)
        claw-observer connect -i

        # Direct mode
        claw-observer connect --ssh root@server --multi

        # Connect to local sidecar
        claw-observer connect ws://localhost:8765
    """
    global _running, _state_renderer

    # Show menu if no options provided
    has_options = any([
        uri is not None,
        ssh is not None,
        token is not None,
        simple,
        multi is not None,
        interactive,
    ])

    if not has_options:
        # No arguments - show main menu instead
        menu()
        return

    # Interactive mode if --interactive flag
    if interactive:
        conn_config = _interactive_connect_setup()
    else:
        conn_config = {
            "uri": uri,
            "ssh": ssh,
            "remote_port": remote_port,
            "local_port": local_port,
            "token": token,
            "simple": simple,
            "multi": multi or False,
        }

    _run_connect(conn_config)

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
    log_source: Optional[str] = typer.Option(
        None,
        "--log-source",
        help="Log source: auto, file:/path, docker:container, journalctl:unit",
    ),
    host: Optional[str] = typer.Option(
        None,
        "--host",
        help="WebSocket server host",
    ),
    port: Optional[int] = typer.Option(
        None,
        "--port",
        help="WebSocket server port",
    ),
    multi: Optional[bool] = typer.Option(
        None,
        "--multi",
        help="Enable multi-agent mode",
    ),
    agents: Optional[str] = typer.Option(
        None,
        "--agents",
        help="Comma-separated list of agent IDs",
    ),
    base_path: Optional[str] = typer.Option(
        None,
        "--base-path",
        help="Base path to OpenClaw agents directory",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Quiet mode (minimal output)",
    ),
    interactive: bool = typer.Option(
        False,
        "-i",
        "--interactive",
        help="Interactive mode with guided setup",
    ),
):
    """
    Start the Observer service (Sidecar mode).

    Run without arguments for interactive setup.

    Examples:

        # Interactive mode (guided setup)
        claw-observer serve -i

        # Direct mode with options
        claw-observer serve --multi --agents main,baba

        # Quiet mode
        claw-observer serve -q
    """
    # Show menu if no options provided
    has_options = any([
        log_source is not None,
        host is not None,
        port is not None,
        multi is not None,
        agents is not None,
        base_path is not None,
        interactive,
    ])

    if not has_options:
        # No arguments - show main menu instead
        menu()
        return

    # Interactive mode if --interactive flag
    if interactive:
        config = _interactive_serve_setup()
    else:
        config = {
            "multi": multi or False,
            "log_source": log_source or "auto",
            "host": host or "0.0.0.0",
            "port": port or 8765,
            "base_path": base_path or "/root/.openclaw/agents",
            "agents": agents,
        }

    _run_serve(config)


def _interactive_connect_setup() -> dict:
    """Interactive setup for connect command."""
    console.print("\n[bold]OpenClaw Observer - Connect Setup[/bold]\n")

    config = {
        "uri": None,
        "ssh": None,
        "remote_port": 8765,
        "local_port": 8765,
        "token": None,
        "simple": False,
        "multi": False,
    }

    # Step 1: Connection type
    conn_type = _prompt_for_option(
        "How do you want to connect?",
        [
            "Direct (connect to ws://localhost:8765)",
            "SSH Tunnel (connect to remote server)",
            "Custom WebSocket URI",
        ],
        "Direct (connect to ws://localhost:8765)",
    )

    if "SSH" in conn_type:
        # SSH tunnel mode
        config["ssh"] = _get_str_input(
            "\nEnter SSH host (e.g., root@180.76.244.82)",
        )
        config["remote_port"] = int(_get_str_input(
            "Remote WebSocket port",
            default="8765",
        ))
        config["local_port"] = int(_get_str_input(
            "Local port for tunnel",
            default="8765",
        ))
    elif "Custom" in conn_type:
        # Custom URI
        config["uri"] = _get_str_input(
            "\nEnter WebSocket URI (e.g., ws://server:8765)",
        )
    # else: Direct connection (uri remains None, will use localhost)

    # Step 2: Authentication
    if _prompt_yes_no("\nUse authentication token?", default=False):
        config["token"] = _get_str_input("Enter JWT token")

    # Step 3: Display mode
    display_mode = _prompt_for_option(
        "\nSelect display mode:",
        [
            "Rich UI (beautiful terminal graphics)",
            "Multi-agent UI (multiple agent panels)",
            "Simple (basic text mode)",
        ],
        "Rich UI (beautiful terminal graphics)",
    )

    config["multi"] = "Multi-agent" in display_mode
    config["simple"] = "Simple" in display_mode

    return config


def _interactive_serve_setup() -> dict:
    """Interactive setup for serve command."""
    console.print("\n[bold]OpenClaw Observer - Server Setup[/bold]\n")

    # Step 1: Choose mode
    mode = _prompt_for_option(
        "Select monitoring mode:",
        ["Single-agent (monitor one log source)", "Multi-agent (monitor multiple agents)"],
        "Multi-agent (monitor multiple agents)",
    )

    is_multi = "multi" in mode.lower()

    config = {
        "multi": is_multi,
        "host": "0.0.0.0",
        "port": 8765,
    }

    if is_multi:
        # Multi-agent mode
        config["base_path"] = _get_str_input(
            "\nPath to agents directory",
            default="/root/.openclaw/agents",
        )

        # Try to discover agents
        import os
        discovered = []
        if os.path.isdir(config["base_path"]):
            for name in os.listdir(config["base_path"]):
                agent_path = os.path.join(config["base_path"], name)
                if os.path.isdir(agent_path):
                    sessions_dir = os.path.join(agent_path, "sessions")
                    if os.path.isdir(sessions_dir):
                        discovered.append(name)

        if discovered:
            console.print(f"\n[green]Discovered agents:[/green] {', '.join(discovered)}")
            all_agents = _prompt_yes_no("Monitor all discovered agents?", default=True)
            if not all_agents:
                selected = _get_str_input(
                    "Enter agent IDs to monitor (comma-separated)",
                    default=",".join(discovered),
                )
                config["agents"] = selected
            else:
                config["agents"] = None  # auto-discover
        else:
            console.print("[yellow]No agents discovered, will auto-discover at runtime[/yellow]")
            config["agents"] = None
    else:
        # Single-agent mode
        log_options = [
            "auto (auto-detect)",
            "file:/var/log/openclaw/gateway.log",
            "docker:openclaw-gateway",
            "journalctl:openclaw-gateway",
        ]
        config["log_source"] = _prompt_for_option(
            "Select log source:",
            log_options,
            "auto (auto-detect)",
        )

    # Advanced settings
    if _prompt_yes_no("Configure advanced settings (host/port)?", default=False):
        config["host"] = _get_str_input("WebSocket bind host", default=config["host"])
        port_input = _get_str_input("WebSocket port", default=str(config["port"]))
        config["port"] = int(port_input)

    return config


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
