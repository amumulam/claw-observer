"""
SSE (Server-Sent Events) Server Module

Provides an SSE server for pushing state changes to clients.
SSE is a simple, HTTP-based protocol for server-to-client push notifications.

Features:
- Standard HTTP (no WebSocket upgrade needed)
- Auto-reconnect support
- CORS support
- JWT authentication via query param
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Set, Optional, Any, Dict, Callable
from pathlib import Path
import uuid

logger = logging.getLogger(__name__)


# Prometheus-style metrics (simple in-memory implementation)
class Metrics:
    """Simple metrics collector."""

    def __init__(self):
        self._counters: Dict[str, int] = {}
        self._gauges: Dict[str, float] = {}
        self._start_time = time.time()

    def inc(self, name: str, value: int = 1, labels: Optional[Dict[str, str]] = None) -> None:
        """Increment a counter."""
        key = self._make_key(name, labels)
        self._counters[key] = self._counters.get(key, 0) + value

    def set_gauge(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """Set a gauge value."""
        key = self._make_key(name, labels)
        self._gauges[key] = value

    def _make_key(self, name: str, labels: Optional[Dict[str, str]] = None) -> str:
        """Create a metric key with optional labels."""
        if labels:
            label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
            return f"{name}{{{label_str}}}"
        return name

    def get_uptime(self) -> float:
        """Get uptime in seconds."""
        return time.time() - self._start_time

    def to_prometheus(self) -> str:
        """Export metrics in Prometheus format."""
        lines = []

        # Counters
        for key, value in self._counters.items():
            lines.append(f"{key} {value}")

        # Gauges
        for key, value in self._gauges.items():
            lines.append(f"{key} {value}")

        # Uptime
        lines.append(f"sidecar_uptime_seconds {self.get_uptime()}")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Export metrics as dictionary."""
        return {
            "counters": self._counters.copy(),
            "gauges": self._gauges.copy(),
            "uptime_seconds": self.get_uptime(),
        }


class SSEClient:
    """Represents a connected SSE client."""

    def __init__(self, client_id: str, writer: asyncio.StreamWriter):
        self.client_id = client_id
        self.writer = writer
        self.connected_at = datetime.now(timezone.utc)
        self.events_sent = 0
        self.last_event_at: Optional[datetime] = None


class SSEServer:
    """
    SSE (Server-Sent Events) server for pushing state changes to clients.

    SSE is a lightweight, HTTP-based push protocol that:
    - Works through firewalls and proxies
    - Auto-reconnects on disconnect
    - Uses standard HTTP ports (80/443)
    - Is simpler than WebSocket

    SSE Message Format:
        event: state_change
        data: {"type": "state_change", "data": {...}}
        id: 123

    See: https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        auth_enabled: bool = False,
        jwt_secret: str = "change-me-in-production",
    ):
        self.host = host
        self.port = port
        self.auth_enabled = auth_enabled
        self.jwt_secret = jwt_secret

        self._clients: Dict[str, SSEClient] = {}
        self._metrics = Metrics()
        self._event_queue: asyncio.Queue[Optional[Event]] = asyncio.Queue()
        self._running = False
        self._server: Optional[asyncio.Server] = None
        self._broadcast_task: Optional[asyncio.Task] = None

        # Callback for state changes
        self._on_state_change: Optional[Callable] = None

        # Store all states for sync on connect
        self._current_states: Dict[str, Dict[str, Any]] = {}  # {agent_id: state_data}

    def on_state_change(self, callback: Callable) -> None:
        """Register callback for state changes."""
        self._on_state_change = callback

    async def start(self) -> None:
        """Start the SSE server."""
        self._running = True

        # Start the event broadcaster
        self._broadcast_task = asyncio.create_task(self._broadcast_events())

        # Start the HTTP server
        self._server = await asyncio.start_server(
            self._handle_connection,
            self.host,
            self.port,
        )

        logger.info(f"SSE server started on http://{self.host}:{self.port}")
        logger.info(f"Connect via: curl http://{self.host}:{self.port}/events")

        # Keep running until stopped
        try:
            async with self._server:
                await self._server.serve_forever()
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the SSE server."""
        self._running = False

        # Stop broadcaster
        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass

        # Close all client connections
        for client in list(self._clients.values()):
            try:
                client.writer.close()
                await client.writer.wait_closed()
            except Exception:
                pass

        # Close the server
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        logger.info("SSE server stopped")

    async def _verify_auth(self, token: str) -> bool:
        """Verify JWT authentication token."""
        import jwt

        if not token:
            return False

        try:
            jwt.decode(token, self.jwt_secret, algorithms=["HS256"])
            return True
        except jwt.ExpiredSignatureError:
            logger.warning("Token expired")
            return False
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token: {e}")
            return False

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle an incoming HTTP connection."""
        try:
            # Read request line
            request_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            request = request_line.decode().strip()

            if not request:
                writer.close()
                await writer.wait_closed()
                return

            # Parse request
            parts = request.split(" ")
            if len(parts) < 2:
                writer.close()
                await writer.wait_closed()
                return

            method, path = parts[0], parts[1]

            # Read headers
            headers = {}
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=10.0)
                if line == b"\r\n" or line == b"":
                    break
                try:
                    key, value = line.decode().strip().split(": ", 1)
                    headers[key.lower()] = value
                except ValueError:
                    pass

            # Route request
            if method == "GET" and path.startswith("/events"):
                # Parse query params for auth token
                token = ""
                if "?" in path:
                    query = path.split("?", 1)[1]
                    for param in query.split("&"):
                        if "=" in param:
                            k, v = param.split("=", 1)
                            if k == "token":
                                token = v

                # Check auth
                if self.auth_enabled and not self._verify_auth(token):
                    await self._send_error(writer, 401, "Unauthorized")
                    return

                # Establish SSE connection
                await self._handle_sse_connection(writer, headers)

            elif method == "GET" and path == "/health":
                health = await self.health_check()
                await self._send_json_response(writer, 200, health)

            elif method == "GET" and path == "/metrics":
                body = self._metrics.to_prometheus().encode()
                await self._send_response(writer, 200, "OK", body, "text/plain")

            elif method == "GET" and path == "/stats":
                stats = {
                    "clients": len(self._clients),
                    "uptime": self._metrics.get_uptime(),
                }
                await self._send_json_response(writer, 200, stats)

            else:
                await self._send_error(writer, 404, "Not Found")

        except asyncio.TimeoutError:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Error handling connection: {e}")
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_sse_connection(
        self,
        writer: asyncio.StreamWriter,
        request_headers: Dict[str, str],
    ) -> None:
        """Handle an SSE connection."""
        # Create client ID
        client_id = str(uuid.uuid4())[:8]

        # Register client
        client = SSEClient(client_id, writer)
        self._clients[client_id] = client
        self._metrics.inc("sse_connections_total")
        self._metrics.set_gauge("sse_connections", len(self._clients))

        logger.info(f"SSE client connected: {client_id} (total: {len(self._clients)})")

        # Send SSE headers
        sse_headers = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/event-stream\r\n"
            "Cache-Control: no-cache\r\n"
            "Connection: keep-alive\r\n"
            "X-Accel-Buffering: no\r\n"  # Disable nginx buffering
            "\r\n"
        )
        writer.write(sse_headers.encode())
        await writer.drain()

        # Send initial sync event with current states
        if self._current_states:
            # Send sync format: {"type": "sync", "states": {agent_id: state_data}}
            # The SSE "event:" field will be "sync", and data will be the JSON
            sync_payload = {
                "type": "sync",
                "states": self._current_states.copy(),
            }
            await self._send_sse_event(
                writer,
                event_type="sync",
                data=json.dumps(sync_payload),
                event_id="0",
            )
            await writer.drain()

        # Keep connection alive
        last_heartbeat = time.time()
        heartbeat_interval = 15  # Send heartbeat every 15 seconds

        try:
            while self._running and client_id in self._clients:
                # Send heartbeat (SSE comment)
                current_time = time.time()
                if current_time - last_heartbeat >= heartbeat_interval:
                    writer.write(b": heartbeat\n\n")
                    await writer.drain()
                    last_heartbeat = current_time

                await asyncio.sleep(1)

        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            logger.info(f"Client {client_id} connection reset")
        except Exception as e:
            logger.error(f"Error in SSE connection {client_id}: {e}")
        finally:
            # Unregister client
            if client_id in self._clients:
                del self._clients[client_id]
                self._metrics.set_gauge("sse_connections", len(self._clients))
                logger.info(f"SSE client disconnected: {client_id} (total: {len(self._clients)})")

            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _send_sse_event(
        self,
        writer: asyncio.StreamWriter,
        event_type: str,
        data: str,
        event_id: Optional[str] = None,
        retry_ms: int = 3000,
    ) -> None:
        """
        Send an SSE event.

        SSE format:
            : comment (optional, for heartbeat)
            event: event_type
            data: {json_data}
            id: event_id
            (blank line)
        """
        event = ""
        if event_id:
            event += f"id: {event_id}\n"
        event += f"event: {event_type}\n"
        event += f"data: {data}\n"
        event += f"retry: {retry_ms}\n\n"

        writer.write(event.encode())
        await writer.drain()

    async def _send_response(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        status_text: str,
        body: bytes,
        content_type: str,
    ) -> None:
        """Send HTTP response."""
        response = (
            f"HTTP/1.1 {status} {status_text}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        )
        writer.write(response.encode())
        writer.write(body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def _send_json_response(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        data: Dict[str, Any],
    ) -> None:
        """Send JSON HTTP response."""
        body = json.dumps(data).encode()
        await self._send_response(writer, status, "OK", body, "application/json")

    async def _send_error(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        message: str,
    ) -> None:
        """Send error response."""
        body = json.dumps({"error": message}).encode()
        await self._send_response(writer, status, "Error", body, "application/json")

    async def _broadcast_events(self) -> None:
        """Broadcast events to all connected clients."""
        event_counter = 0

        while self._running:
            try:
                # Get event from queue
                event = await self._event_queue.get()

                if event is None:
                    # Shutdown signal
                    break

                event_counter += 1
                event_data = event.to_dict()

                # Store current state for sync
                if event_data.get("type") == "state_change":
                    agent_id = event_data.get("agent_id", "default")
                    self._current_states[agent_id] = event_data

                # Broadcast to all clients
                disconnected = []

                for client_id, client in list(self._clients.items()):
                    try:
                        message = json.dumps(event_data)
                        await self._send_sse_event(
                            client.writer,
                            event_type=event_data.get("type", "event"),
                            data=message,
                            event_id=str(event_counter),
                        )
                        client.events_sent += 1
                        client.last_event_at = datetime.now(timezone.utc)
                        self._metrics.inc("sse_events_sent_total")

                    except ConnectionResetError:
                        disconnected.append(client_id)
                        logger.info(f"Client {client_id} connection reset")
                    except Exception as e:
                        disconnected.append(client_id)
                        logger.error(f"Error sending to client {client_id}: {e}")

                # Remove disconnected clients
                for client_id in disconnected:
                    if client_id in self._clients:
                        del self._clients[client_id]

                if disconnected:
                    self._metrics.set_gauge("sse_connections", len(self._clients))

                self._event_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in broadcaster: {e}")

    def push_event(self, event: Any) -> None:
        """
        Push an event to all connected clients.

        This method is thread-safe and can be called from any thread.
        """
        self._event_queue.put_nowait(event)

    def on_parser_event(self, event: Any) -> None:
        """Callback for parser events - pushes to SSE clients."""
        self.push_event(event)

    def on_parser_event_multi(self, agent_id: str, event: Any) -> None:
        """Callback for multi-agent parser events - adds agent_id and pushes."""
        event.agent_id = agent_id
        self.push_event(event)

    async def health_check(self) -> Dict[str, Any]:
        """Get health check status."""
        return {
            "status": "healthy" if self._running else "unhealthy",
            "clients_connected": len(self._clients),
            "uptime_seconds": self._metrics.get_uptime(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_metrics(self) -> Metrics:
        """Get metrics collector."""
        return self._metrics


# Import Event type for type hints
from .rules.base import Event


class HTTPServer:
    """
    Simple HTTP server for health checks and metrics.

    Note: This is now deprecated as SSEServer handles HTTP directly.
    """

    def __init__(self, sse_server: SSEServer):
        self.sse_server = sse_server

    async def start(self, host: str = "0.0.0.0", port: int = 8766) -> None:
        """Start HTTP server on a different port (if needed)."""
        logger.warning("HTTPServer is deprecated - SSEServer handles HTTP directly")
