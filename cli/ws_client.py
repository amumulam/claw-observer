"""
WebSocket Client Module

Connects to the Sidecar WebSocket server and receives state changes.
"""

import asyncio
import json
import logging
from typing import Optional, Callable, Any
from datetime import datetime

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatusCode

logger = logging.getLogger(__name__)


class WebSocketClient:
    """
    WebSocket client for connecting to the Sidecar server.

    Features:
    - Auto-reconnect with exponential backoff
    - Event callbacks
    - Connection status tracking
    """

    def __init__(
        self,
        uri: str,
        auth_token: Optional[str] = None,
        max_reconnect_delay: int = 60,
    ):
        self.uri = uri
        self.auth_token = auth_token
        self.max_reconnect_delay = max_reconnect_delay

        self._websocket: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._connected = False
        self._reconnect_delay = 1.0

        # Callbacks
        self._event_callbacks: list[Callable[[dict], None]] = []
        self._connect_callbacks: list[Callable[[], None]] = []
        self._disconnect_callbacks: list[Callable[[], None]] = []

        # Stats
        self._messages_received = 0
        self._reconnect_count = 0
        self._last_message_time: Optional[datetime] = None

        # Track if we've ever connected (to avoid flickering on initial connect)
        self._ever_connected = False

    def on_event(self, callback: Callable[[dict], None]) -> None:
        """Register callback for received events."""
        self._event_callbacks.append(callback)

    def on_connect(self, callback: Callable[[], None]) -> None:
        """Register callback for connection established."""
        self._connect_callbacks.append(callback)

    def on_disconnect(self, callback: Callable[[], None]) -> None:
        """Register callback for connection lost."""
        self._disconnect_callbacks.append(callback)

    @property
    def is_connected(self) -> bool:
        """Check if currently connected."""
        return self._connected

    @property
    def stats(self) -> dict:
        """Get connection statistics."""
        return {
            "connected": self._connected,
            "messages_received": self._messages_received,
            "reconnect_count": self._reconnect_count,
            "last_message_time": (
                self._last_message_time.isoformat() if self._last_message_time else None
            ),
        }

    async def connect(self) -> None:
        """
        Connect to the WebSocket server.

        Auto-reconnects on failure.
        """
        self._running = True

        while self._running:
            try:
                await self._connect()
            except ConnectionClosed:
                logger.info("Connection closed")
                self._handle_disconnect()
            except InvalidStatusCode as e:
                logger.error(f"Connection failed: {e}")
                self._handle_disconnect()
            except Exception as e:
                logger.error(f"Connection error: {e}")
                self._handle_disconnect()

            if self._running and not self._connected:
                # Reconnect with exponential backoff
                logger.info(f"Reconnecting in {self._reconnect_delay}s...")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2,
                    self.max_reconnect_delay,
                )
                self._reconnect_count += 1

    async def _connect(self) -> None:
        """Establish WebSocket connection."""
        headers = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        logger.info(f"Connecting to {self.uri}...")

        async with websockets.connect(
            self.uri,
            extra_headers=headers,
            ping_interval=30,
            ping_timeout=10,
        ) as websocket:
            self._websocket = websocket
            self._connected = True
            self._ever_connected = True  # Mark that we've successfully connected
            self._reconnect_delay = 1.0  # Reset delay on successful connect

            logger.info(f"Connected to {self.uri}")

            # Notify connect callbacks
            for callback in self._connect_callbacks:
                try:
                    callback()
                except Exception as e:
                    logger.error(f"Error in connect callback: {e}")

            # Receive messages
            await self._receive_messages()

    async def _receive_messages(self) -> None:
        """Receive and process WebSocket messages."""
        async for message in self._websocket:
            if not self._running:
                break

            try:
                data = json.loads(message)
                self._messages_received += 1
                self._last_message_time = datetime.utcnow()

                # Notify event callbacks
                for callback in self._event_callbacks:
                    try:
                        callback(data)
                    except Exception as e:
                        logger.error(f"Error in event callback: {e}")

            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON received: {message}")

    def _handle_disconnect(self) -> None:
        """Handle disconnection."""
        if self._connected:
            self._connected = False
            self._websocket = None

            # Only notify disconnect if we were previously connected
            # This prevents flickering during initial connection attempts
            if self._ever_connected:
                # Notify disconnect callbacks
                for callback in self._disconnect_callbacks:
                    try:
                        callback()
                    except Exception as e:
                        logger.error(f"Error in disconnect callback: {e}")

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        self._running = False

        if self._websocket:
            await self._websocket.close()
            self._websocket = None

        self._connected = False
        logger.info("Disconnected")

    async def send(self, message: dict) -> None:
        """Send a message to the server."""
        if not self._connected or not self._websocket:
            raise RuntimeError("Not connected")

        await self._websocket.send(json.dumps(message))

    async def send_ack(self, message_id: str) -> None:
        """Send acknowledgment for a message."""
        await self.send({
            "type": "ack",
            "message_id": message_id,
            "received_at": datetime.utcnow().isoformat() + "Z",
        })


class StateClient:
    """
    High-level client for receiving state changes.

    Wraps WebSocketClient and provides state-specific callbacks.
    """

    def __init__(
        self,
        uri: str,
        auth_token: Optional[str] = None,
    ):
        self._client = WebSocketClient(uri, auth_token)
        self._current_state: Optional[str] = None
        self._state_callbacks: list[Callable[[str, str, dict], None]] = []

        # Register event handler
        self._client.on_event(self._handle_event)

    def on_state_change(
        self,
        callback: Callable[[str, str, dict], None],
    ) -> None:
        """
        Register callback for state changes.

        Callback receives: (previous_state, new_state, meta)
        """
        self._state_callbacks.append(callback)

    @property
    def current_state(self) -> Optional[str]:
        """Get the current state."""
        return self._current_state

    @property
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._client.is_connected

    def _handle_event(self, data: dict) -> None:
        """Handle incoming event."""
        event_type = data.get("type", "")

        if event_type == "state_change":
            event_data = data.get("data", {})
            new_state = event_data.get("state", "")
            previous_state = event_data.get("previous_state", "")
            meta = event_data.copy()
            meta.pop("state", None)
            meta.pop("previous_state", None)

            self._current_state = new_state

            # Notify callbacks
            for callback in self._state_callbacks:
                try:
                    callback(previous_state, new_state, meta)
                except Exception as e:
                    logger.error(f"Error in state callback: {e}")

    async def connect(self) -> None:
        """Connect to the server."""
        await self._client.connect()

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        await self._client.disconnect()

    @property
    def stats(self) -> dict:
        """Get client statistics."""
        client_stats = self._client.stats
        client_stats["current_state"] = self._current_state
        return client_stats


class MultiAgentStateClient:
    """
    High-level client for receiving state changes from multiple agents.

    Wraps WebSocketClient and provides multi-agent state callbacks.
    """

    def __init__(
        self,
        uri: str,
        auth_token: Optional[str] = None,
    ):
        self._client = WebSocketClient(uri, auth_token)
        self._agent_states: dict[str, str] = {}  # {agent_id: state}
        self._state_callbacks: list[Callable[[str, str, str, dict], None]] = []

        # Register event handler
        self._client.on_event(self._handle_event)

    def on_state_change(
        self,
        callback: Callable[[str, str, str, dict], None],
    ) -> None:
        """
        Register callback for state changes.

        Callback receives: (agent_id, previous_state, new_state, meta)
        """
        self._state_callbacks.append(callback)

    @property
    def agent_states(self) -> dict[str, str]:
        """Get states of all agents."""
        return self._agent_states.copy()

    def get_agent_state(self, agent_id: str) -> Optional[str]:
        """Get the current state of a specific agent."""
        return self._agent_states.get(agent_id)

    @property
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._client.is_connected

    def _handle_event(self, data: dict) -> None:
        """Handle incoming event."""
        event_type = data.get("type", "")

        if event_type == "state_change":
            event_data = data.get("data", {})
            agent_id = data.get("agent_id")

            if not agent_id:
                # Fallback to single-agent mode
                agent_id = "default"

            new_state = event_data.get("state", "")
            previous_state = event_data.get("previous_state", "")
            meta = event_data.copy()
            meta.pop("state", None)
            meta.pop("previous_state", None)

            self._agent_states[agent_id] = new_state

            # Notify callbacks
            for callback in self._state_callbacks:
                try:
                    callback(agent_id, previous_state, new_state, meta)
                except Exception as e:
                    logger.error(f"Error in state callback: {e}")

    async def connect(self) -> None:
        """Connect to the server."""
        await self._client.connect()

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        await self._client.disconnect()

    @property
    def stats(self) -> dict:
        """Get client statistics."""
        client_stats = self._client.stats
        client_stats["agent_count"] = len(self._agent_states)
        client_stats["agents"] = self._agent_states.copy()
        return client_stats
