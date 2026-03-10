"""
SSE (Server-Sent Events) Client Module

Connects to the Sidecar SSE server and receives state changes.

SSE is a lightweight, HTTP-based push protocol that:
- Works through firewalls and proxies (uses standard HTTP ports)
- Auto-reconnects on disconnect
- Is simpler than WebSocket
- Is natively supported in browsers
"""

import asyncio
import json
import logging
from typing import Optional, Callable, Any
from datetime import datetime
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)


class SSEClient:
    """
    SSE (Server-Sent Events) client for connecting to the Sidecar server.

    Features:
    - Auto-reconnect with exponential backoff
    - Event callbacks
    - Connection status tracking
    - Heartbeat detection
    """

    def __init__(
        self,
        uri: str,
        auth_token: Optional[str] = None,
        max_reconnect_delay: int = 60,
        connect_timeout: int = 10,
    ):
        # Convert ws:// to http:// and wss:// to https://
        if uri.startswith("ws://"):
            uri = uri.replace("ws://", "http://", 1)
        elif uri.startswith("wss://"):
            uri = uri.replace("wss://", "https://", 1)

        # Build SSE endpoint URI
        if "?" in uri:
            self.uri = f"{uri}&token={auth_token}" if auth_token else uri
        else:
            self.uri = f"{uri}/events?token={auth_token}" if auth_token else f"{uri}/events"

        self.auth_token = auth_token
        self.max_reconnect_delay = max_reconnect_delay
        self.connect_timeout = connect_timeout

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
        self._ever_connected = False

        # Current connection
        self._response: Optional[urllib.request.Addinfourl] = None
        self._reader: Optional[any] = None

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
        Connect to the SSE server.

        Auto-reconnects on failure.
        """
        self._running = True

        while self._running:
            try:
                await self._connect()
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
        """Establish SSE connection."""
        logger.info(f"Connecting to {self.uri}...")

        # Use aiohttp for async HTTP streaming
        try:
            import aiohttp
        except ImportError:
            logger.error("aiohttp not installed. Install with: pip install aiohttp")
            # Fallback to synchronous approach
            await self._connect_sync()
            return

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(self.uri, timeout=aiohttp.ClientTimeout(total=None)) as response:
                    if response.status != 200:
                        raise Exception(f"SSE server returned status {response.status}")

                    self._connected = True
                    self._ever_connected = True
                    self._reconnect_delay = 1.0

                    logger.info(f"Connected to {self.uri}")

                    # Notify connect callbacks
                    for callback in self._connect_callbacks:
                        try:
                            callback()
                        except Exception as e:
                            logger.error(f"Error in connect callback: {e}")

                    # Read SSE events
                    async for line in response.content:
                        if not self._running:
                            break

                        line = line.decode("utf-8").strip()
                        await self._process_sse_line(line)

            except aiohttp.ClientError as e:
                raise Exception(f"Connection failed: {e}")
            finally:
                self._connected = False

    async def _connect_sync(self) -> None:
        """Synchronous fallback for SSE connection."""
        # Run synchronous connection in executor
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._connect_sync_impl)

    def _connect_sync_impl(self) -> None:
        """Synchronous SSE connection implementation."""
        logger.info(f"Connecting to {self.uri}...")

        try:
            # Build request with timeout
            request = urllib.request.Request(self.uri)
            request.add_header("Accept", "text/event-stream")
            request.add_header("Cache-Control", "no-cache")

            # Open connection
            self._response = urllib.request.urlopen(request, timeout=self.connect_timeout)
            self._reader = self._response

            self._connected = True
            self._ever_connected = True
            self._reconnect_delay = 1.0

            logger.info(f"Connected to {self.uri}")

            # Notify connect callbacks (in event loop)
            loop = asyncio.get_event_loop()
            for callback in self._connect_callbacks:
                try:
                    loop.call_soon_threadsafe(callback)
                except Exception as e:
                    logger.error(f"Error in connect callback: {e}")

            # Read SSE events line by line
            current_event = {}
            last_heartbeat = time.time()

            while self._running:
                try:
                    line = self._reader.readline().decode("utf-8").strip()
                except Exception as e:
                    # Connection error
                    logger.error(f"Read error: {e}")
                    break

                if not line:
                    # Empty line = end of event
                    if current_event.get("data"):
                        self._dispatch_event(current_event)
                    current_event = {}
                    continue

                if line.startswith(":"):
                    # Comment/heartbeat
                    last_heartbeat = time.time()
                    continue

                if ":" in line:
                    key, value = line.split(":", 1)
                    key = key.strip()
                    value = value.strip()

                    if key == "event":
                        current_event["type"] = value
                    elif key == "data":
                        current_event["data"] = value
                    elif key == "id":
                        current_event["id"] = value
                    elif key == "retry":
                        current_event["retry"] = int(value)

        except Exception as e:
            logger.error(f"Sync connection error: {e}")
            raise
        finally:
            self._connected = False
            if self._response:
                self._response.close()

    def _dispatch_event(self, event: dict) -> None:
        """Dispatch an SSE event to callbacks."""
        try:
            data_str = event.get("data", "{}")
            data = json.loads(data_str)
            self._messages_received += 1
            self._last_message_time = datetime.utcnow()

            # Notify event callbacks
            for callback in self._event_callbacks:
                try:
                    callback(data)
                except Exception as e:
                    logger.error(f"Error in event callback: {e}")
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in event: {event.get('data')}")

    async def _process_sse_line(self, line: str) -> None:
        """Process a single SSE event line."""
        # SSE format:
        # : comment (heartbeat)
        # event: event_type
        # data: {json}
        # id: event_id
        # (blank line)

        line = line.decode("utf-8").strip() if isinstance(line, bytes) else line.strip()

        if not line:
            # End of event - dispatch
            if hasattr(self, "_current_event") and self._current_event:
                self._dispatch_event(self._current_event)
                self._current_event = {}
            return

        if line.startswith(":"):
            # Comment/heartbeat - ignore
            return

        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()

            if not hasattr(self, "_current_event"):
                self._current_event = {}

            if key == "event":
                self._current_event["type"] = value
            elif key == "data":
                self._current_event["data"] = value
            elif key == "id":
                self._current_event["id"] = value
            elif key == "retry":
                self._current_event["retry"] = int(value)

    def _handle_disconnect(self) -> None:
        """Handle disconnection."""
        if self._connected:
            self._connected = False
            self._response = None
            self._reader = None

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

        if self._response:
            try:
                self._response.close()
            except Exception:
                pass

        self._connected = False
        logger.info("Disconnected")


class StateClient:
    """
    High-level client for receiving state changes.

    Wraps SSEClient and provides state-specific callbacks.
    """

    def __init__(
        self,
        uri: str,
        auth_token: Optional[str] = None,
    ):
        self._client = SSEClient(uri, auth_token)
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

        if event_type == "sync":
            # Handle sync event - receive all current states
            sync_data = data.get("data", {})
            states = sync_data.get("states", {})
            logger.info(f"Received sync: {len(states)} states")
            for agent_id, state_data in states.items():
                new_state = state_data.get("state", "")
                if new_state:
                    self._current_state = new_state
                    for callback in self._state_callbacks:
                        try:
                            callback("", new_state, state_data)
                        except Exception as e:
                            logger.error(f"Error in sync callback: {e}")

        elif event_type == "state_change":
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

    Wraps SSEClient and provides multi-agent state callbacks.
    """

    def __init__(
        self,
        uri: str,
        auth_token: Optional[str] = None,
    ):
        self._client = SSEClient(uri, auth_token)
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

        if event_type == "sync":
            # Handle sync event - receive all current states
            # Format: {"type": "sync", "data": {"states": {agent_id: state_data}}}
            sync_data = data.get("data", {})
            states = sync_data.get("states", {})
            logger.info(f"Received sync: {len(states)} agent states")
            for agent_id, state_data in states.items():
                new_state = state_data.get("state", "")
                if new_state:
                    self._agent_states[agent_id] = new_state
                    for callback in self._state_callbacks:
                        try:
                            callback(agent_id, "", new_state, state_data)
                        except Exception as e:
                            logger.error(f"Error in sync callback: {e}")

        elif event_type == "state_change":
            # Format: {"type": "state_change", "agent_id": "xxx", "data": {...}}
            event_data = data.get("data", {})
            agent_id = data.get("agent_id", "default")

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


# Import time for sync mode
import time
