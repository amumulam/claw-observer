"""
Terminal UI Renderer Module

Renders the OpenClaw state to the terminal using Rich.
"""

from datetime import datetime
from typing import Optional, Dict, Any

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.layout import Layout
from rich.text import Text
from rich.style import Style


# State colors
STATE_COLORS = {
    "IDLE": "green",
    "THINKING": "yellow",
    "REPLYING": "cyan",
    "EXECUTING": "magenta",
    "ERROR": "red",
}

# State icons
STATE_ICONS = {
    "IDLE": "○",
    "THINKING": "◐",
    "REPLYING": "●",
    "EXECUTING": "⚙",
    "ERROR": "✖",
}


class StateRenderer:
    """
    Renders OpenClaw state to the terminal.

    Uses Rich for beautiful terminal output.
    """

    def __init__(self, instance_name: str = "OpenClaw Gateway"):
        self.console = Console()
        self.instance_name = instance_name

        # Current state
        self._current_state = "IDLE"
        self._previous_state = None
        self._state_meta: Dict[str, Any] = {}
        self._last_update: Optional[datetime] = None
        self._connection_status = "disconnected"

        # Statistics
        self._events_received = 0
        self._start_time = datetime.utcnow()

        # Layout
        self._layout = self._create_layout()
        self._live: Optional[Live] = None

    def _create_layout(self) -> Layout:
        """Create the terminal layout."""
        layout = Layout()

        layout.split(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=3),
        )

        layout["body"].split(
            Layout(name="state_panel"),
            Layout(name="details_panel", size=10),
        )

        return layout

    def update_state(
        self,
        state: str,
        previous_state: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Update the current state.

        Args:
            state: New state name
            previous_state: Previous state name
            meta: Additional metadata
        """
        self._previous_state = previous_state
        self._current_state = state.upper()
        self._state_meta = meta or {}
        self._last_update = datetime.utcnow()
        self._events_received += 1

        if self._live:
            self._live.update(self._render())

    def set_connection_status(self, status: str) -> None:
        """Set connection status (connected/disconnected/reconnecting)."""
        self._connection_status = status
        if self._live:
            self._live.update(self._render())

    def set_tool_details(self, tool_name: str, action: str, params: Optional[Dict] = None) -> None:
        """Set tool execution details."""
        self._state_meta["tool_name"] = tool_name
        self._state_meta["action"] = action
        if params:
            self._state_meta["params"] = params

    def _get_state_style(self, state: str) -> Style:
        """Get the style for a state."""
        color = STATE_COLORS.get(state, "white")
        return Style(color=color, bold=True)

    def _render_header(self) -> Table:
        """Render the header."""
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("Title", style="bold white")
        table.add_column("Status", justify="right")

        status_color = "green" if self._connection_status == "connected" else "red"
        status_text = f"[{status_color}]●[/{status_color}] {self._connection_status.title()}"

        table.add_row(
            f"OpenClaw Monitor - {self.instance_name}",
            status_text,
        )

        return table

    def _render_state_panel(self) -> Panel:
        """Render the main state display."""
        state = self._current_state
        color = STATE_COLORS.get(state, "white")
        icon = STATE_ICONS.get(state, "○")

        # Create state text with animation effect
        state_text = Text()
        state_text.append(f"{icon} ", style=color)
        state_text.append(state, style=Style(color=color, bold=True))

        # Add previous state if available
        if self._previous_state:
            state_text.append(f" (from {self._previous_state})", style="dim")

        # Create panel
        panel = Panel(
            state_text,
            title="[bold]Current State[/bold]",
            border_style=color,
            padding=(1, 2),
        )

        return panel

    def _render_details_panel(self) -> Panel:
        """Render the details panel."""
        lines = []

        # Tool information
        if "tool_name" in self._state_meta:
            tool_name = self._state_meta["tool_name"]
            action = self._state_meta.get("action", "unknown")
            lines.append(f"[bold]Tool:[/] {tool_name}")
            lines.append(f"[bold]Action:[/] {action}")

            # Parameters
            params = self._state_meta.get("params", {})
            if params:
                lines.append("")
                lines.append("[bold]Parameters:[/]")
                for key, value in params.items():
                    lines.append(f"  • {key}: {value}")

        # Error information
        if "message" in self._state_meta and self._current_state == "ERROR":
            lines.append("")
            lines.append(f"[bold red]Error:[/] {self._state_meta['message']}")

        # Last update time
        if self._last_update:
            lines.append("")
            lines.append(f"[dim]Last update: {self._last_update.strftime('%H:%M:%S')}[/dim]")

        content = "\n".join(lines) if lines else "[dim]No details available[/dim]"

        panel = Panel(
            content,
            title="[bold]Details[/bold]",
            border_style="white",
            padding=(1, 2),
        )

        return panel

    def _render_footer(self) -> Table:
        """Render the footer."""
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("Stats", style="dim")
        table.add_column("Uptime", justify="right", style="dim")

        # Calculate uptime
        uptime = datetime.utcnow() - self._start_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        table.add_row(
            f"Events: {self._events_received} | State: {self._current_state}",
            f"Uptime: {uptime_str}",
        )

        return table

    def _render(self) -> Layout:
        """Render the complete layout."""
        layout = self._create_layout()

        layout["header"].update(self._render_header())
        layout["state_panel"].update(self._render_state_panel())
        layout["details_panel"].update(self._render_details_panel())
        layout["footer"].update(self._render_footer())

        return layout

    def start(self) -> None:
        """Start the live display."""
        self._live = Live(self._render(), console=self.console, refresh_per_second=4)
        self._live.start()

    def stop(self) -> None:
        """Stop the live display."""
        if self._live:
            self._live.stop()
            self._live = None

    def render_once(self) -> None:
        """Render once without live updates."""
        self.console.print(self._render())


class SimpleRenderer:
    """
    Simple line-based renderer for basic terminals.

    Falls back when Rich is not available or terminal is too small.
    """

    def __init__(self):
        self._current_state = "IDLE"
        self._events_received = 0
        self._start_time = datetime.utcnow()

    def update_state(
        self,
        state: str,
        previous_state: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Update state and print."""
        self._current_state = state.upper()
        self._events_received += 1

        icon = STATE_ICONS.get(self._current_state, "○")
        color = STATE_COLORS.get(self._current_state, "white")

        print(f"\r{icon} [{color}]{self._current_state}[/{color}]  Events: {self._events_received}", end="")

    def set_connection_status(self, status: str) -> None:
        """Set connection status."""
        pass  # Not supported in simple mode

    def start(self) -> None:
        """Start display."""
        print("OpenClaw Monitor (Simple Mode)")
        print("-" * 40)

    def stop(self) -> None:
        """Stop display."""
        print()

    def render_once(self) -> None:
        """Render once."""
        self.start()


class MultiAgentStateRenderer:
    """
    Renders multi-agent OpenClaw states to the terminal.

    Displays a panel for each agent being monitored.
    """

    def __init__(self, instance_name: str = "OpenClaw Gateway"):
        self.console = Console()
        self.instance_name = instance_name

        # Agent states: {agent_id: {state, previous_state, meta, last_update}}
        self._agent_states: Dict[str, Dict[str, Any]] = {}
        self._connection_status = "disconnected"

        # Statistics
        self._events_received = 0
        self._start_time = datetime.utcnow()

        # Layout
        self._live: Optional[Live] = None

    def update_state(
        self,
        agent_id: str,
        state: str,
        previous_state: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Update the state for a specific agent.

        Args:
            agent_id: Agent identifier
            state: New state name
            previous_state: Previous state name
            meta: Additional metadata
        """
        if agent_id not in self._agent_states:
            self._agent_states[agent_id] = {
                "state": state.upper(),
                "previous_state": previous_state,
                "meta": {},
                "last_update": datetime.utcnow(),
            }
        else:
            self._agent_states[agent_id]["state"] = state.upper()
            self._agent_states[agent_id]["previous_state"] = previous_state
            self._agent_states[agent_id]["meta"] = meta or {}
            self._agent_states[agent_id]["last_update"] = datetime.utcnow()

        self._events_received += 1

        if self._live:
            self._live.update(self._render())

    def set_connection_status(self, status: str) -> None:
        """Set connection status (connected/disconnected/reconnecting)."""
        self._connection_status = status
        if self._live:
            self._live.update(self._render())

    def _get_state_style(self, state: str) -> Style:
        """Get the style for a state."""
        color = STATE_COLORS.get(state, "white")
        return Style(color=color, bold=True)

    def _render_header(self) -> Table:
        """Render the header."""
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("Title", style="bold white")
        table.add_column("Status", justify="right")

        status_color = "green" if self._connection_status == "connected" else "red"
        status_text = f"[{status_color}]●[/{status_color}] {self._connection_status.title()}"

        table.add_row(
            f"OpenClaw Monitor - {self.instance_name} ({len(self._agent_states)} agents)",
            status_text,
        )

        return table

    def _render_agent_panel(self, agent_id: str) -> Panel:
        """Render a panel for a single agent."""
        agent_data = self._agent_states.get(agent_id, {})
        state = agent_data.get("state", "IDLE")
        previous_state = agent_data.get("previous_state")
        meta = agent_data.get("meta", {})
        last_update = agent_data.get("last_update")

        color = STATE_COLORS.get(state, "white")
        icon = STATE_ICONS.get(state, "○")

        # Create state text
        state_text = Text()
        state_text.append(f"{icon} ", style=color)
        state_text.append(f"[bold]{agent_id}[/bold]: {state}", style=Style(color=color, bold=True))

        if previous_state:
            state_text.append(f" (from {previous_state})", style="dim")

        # Add tool info if available
        details = []
        if "tool_name" in meta:
            details.append(f"Tool: {meta['tool_name']}")
        if "action" in meta:
            details.append(f"Action: {meta['action']}")
        if "error" in meta:
            details.append(f"[red]Error: {meta['error']}[/red]")

        if details:
            state_text.append("\n")
            state_text.append("\n".join(details), style="dim")

        if last_update:
            state_text.append(f"\nLast update: {last_update.strftime('%H:%M:%S')}", style="dim")

        panel = Panel(
            state_text,
            title=f"[bold]Agent: {agent_id}[/bold]",
            border_style=color,
            padding=(1, 2),
        )

        return panel

    def _render_agents_grid(self) -> Table:
        """Render a grid of agent panels."""
        if not self._agent_states:
            return Table(show_header=False, box=None)

        # Create a table with 2 columns
        table = Table(show_header=False, box=None, expand=True)
        table.add_column("Agents", ratio=1)

        for agent_id in sorted(self._agent_states.keys()):
            panel = self._render_agent_panel(agent_id)
            table.add_row(panel)

        return table

    def _render_footer(self) -> Table:
        """Render the footer."""
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("Stats", style="dim")
        table.add_column("Uptime", justify="right", style="dim")

        # Calculate uptime
        uptime = datetime.utcnow() - self._start_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        table.add_row(
            f"Events: {self._events_received} | Agents: {len(self._agent_states)}",
            f"Uptime: {uptime_str}",
        )

        return table

    def _render(self) -> Layout:
        """Render the complete layout."""
        layout = Layout()

        layout.split(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=3),
        )

        layout["header"].update(self._render_header())
        layout["body"].update(self._render_agents_grid())
        layout["footer"].update(self._render_footer())

        return layout

    def start(self) -> None:
        """Start the live display."""
        self._live = Live(self._render(), console=self.console, refresh_per_second=4)
        self._live.start()

    def stop(self) -> None:
        """Stop the live display."""
        if self._live:
            self._live.stop()
            self._live = None

    def render_once(self) -> None:
        """Render once without live updates."""
        self.console.print(self._render())
