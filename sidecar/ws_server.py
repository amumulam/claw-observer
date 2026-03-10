"""
WebSocket Server Module

Provides a WebSocket server for pushing state changes to clients.
Also provides HTTP endpoints for health checks and metrics.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Set, Optional, Any, Dict, Callable
from pathlib import Path

import websockets
from websockets.server import WebSocketServerProtocol
from websockets.exceptions import ConnectionClosed

from .state_machine import StateChange
from .rules.base import Event

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


class WebSocketServer:
    """
    WebSocket server for pushing state changes to clients.

    Also handles HTTP requests for health checks and metrics.
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

        self._clients: Set[WebSocketServerProtocol] = set()
        self._metrics = Metrics()
        self._event_queue: asyncio.Queue[Optional[Event]] = asyncio.Queue()
        self._running = False
        self._server: Optional[websockets.WebSocketServerProtocol] = None

        # Callback for state changes
        self._on_state_change: Optional[Callable[[StateChange], None]] = None

    def on_state_change(self, callback: Callable[[StateChange], None]) -> None:
        """Register callback for state changes."""
        self._on_state_change = callback

    async def start(self) -> None:
        """Start the WebSocket server."""
        self._running = True

        # Start the event broadcaster
        broadcaster_task = asyncio.create_task(self._broadcast_events())

        # Start the WebSocket server
        async with websockets.serve(
            self._handle_connection,
            self.host,
            self.port,
            ping_interval=30,
            ping_timeout=10,
        ) as server:
            self._server = server
            logger.info(f"WebSocket server started on ws://{self.host}:{self.port}")

            # Keep running until stopped
            try:
                await asyncio.Future()  # Run forever
            except asyncio.CancelledError:
                pass
            finally:
                self._running = False
                broadcaster_task.cancel()
                try:
                    await broadcaster_task
                except asyncio.CancelledError:
                    pass

    async def stop(self) -> None:
        """Stop the WebSocket server."""
        self._running = False

        # Close all client connections
        for client in self._clients.copy():
            try:
                await client.close(1001, "Server shutting down")
            except Exception:
                pass

        # Close the server
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        logger.info("WebSocket server stopped")

    async def _handle_connection(self, websocket: WebSocketServerProtocol) -> None:
        """Handle a new WebSocket connection."""
        # Check authentication
        if self.auth_enabled:
            try:
                await self._verify_auth(websocket)
            except Exception as e:
                logger.warning(f"Authentication failed: {e}")
                await websocket.close(1008, "Authentication failed")
                return

        # Register client
        self._clients.add(websocket)
        self._metrics.inc("sidecar_ws_connections_total")
        self._metrics.set_gauge("sidecar_ws_connections", len(self._clients))

        client_id = id(websocket)
        logger.info(f"Client connected: {client_id} (total: {len(self._clients)})")

        try:
            # Handle incoming messages (mostly for acks)
            async for message in websocket:
                await self._handle_message(websocket, message)
        except ConnectionClosed as e:
            logger.info(f"Client disconnected: {client_id} (code: {e.code})")
        except Exception as e:
            logger.error(f"Error handling client {client_id}: {e}")
        finally:
            # Unregister client
            self._clients.discard(websocket)
            self._metrics.set_gauge("sidecar_ws_connections", len(self._clients))
            logger.info(f"Client disconnected: {client_id} (total: {len(self._clients)})")

    async def _verify_auth(self, websocket: WebSocketServerProtocol) -> None:
        """Verify JWT authentication."""
        import jwt

        # Get authorization header
        auth_header = websocket.request_headers.get("Authorization", "")

        if not auth_header.startswith("Bearer "):
            raise ValueError("Missing or invalid Authorization header")

        token = auth_header[7:]  # Remove "Bearer " prefix

        # Verify JWT
        try:
            jwt.decode(token, self.jwt_secret, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            raise ValueError("Token expired")
        except jwt.InvalidTokenError as e:
            raise ValueError(f"Invalid token: {e}")

    async def _handle_message(self, websocket: WebSocketServerProtocol, message: str) -> None:
        """Handle an incoming WebSocket message."""
        try:
            data = json.loads(message)
            msg_type = data.get("type", "")

            if msg_type == "ack":
                # Acknowledgment received
                self._metrics.inc("sidecar_ws_acks_total")
                logger.debug(f"Received ack from {id(websocket)}")

        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON from client: {message}")

    async def _broadcast_events(self) -> None:
        """Broadcast events to all connected clients."""
        while self._running:
            try:
                # Get event from queue
                event = await self._event_queue.get()

                if event is None:
                    # Shutdown signal
                    break

                # Broadcast to all clients
                if self._clients:
                    message = json.dumps(event.to_dict())
                    disconnected = set()

                    for client in self._clients:
                        try:
                            await client.send(message)
                            self._metrics.inc("sidecar_ws_messages_sent_total")
                        except ConnectionClosed:
                            disconnected.add(client)
                        except Exception as e:
                            logger.error(f"Error sending to client: {e}")
                            disconnected.add(client)

                    # Remove disconnected clients
                    for client in disconnected:
                        self._clients.discard(client)
                    if disconnected:
                        self._metrics.set_gauge("sidecar_ws_connections", len(self._clients))

                self._event_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in broadcaster: {e}")

    def push_event(self, event: Event) -> None:
        """
        Push an event to all connected clients.

        This method is thread-safe and can be called from any thread.
        """
        self._event_queue.put_nowait(event)

    def on_parser_event(self, event: Event) -> None:
        """Callback for parser events - pushes to WebSocket clients."""
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

    async def handle_http_request(
        self,
        path: str,
        request_headers: Any,
    ) -> Optional[tuple]:
        """
        Handle HTTP requests for health checks and metrics.

        This is called by websockets library for non-WebSocket requests.
        """
        if path == "/health":
            health = await self.health_check()
            return (
                200,
                [("Content-Type", "application/json")],
                json.dumps(health).encode(),
            )
        elif path == "/metrics":
            metrics_text = self._metrics.to_prometheus()
            return (
                200,
                [("Content-Type", "text/plain")],
                metrics_text.encode(),
            )
        elif path == "/stats":
            from .parser import LogParser

            # Return parser stats if available
            stats = {
                "clients": len(self._clients),
                "uptime": self._metrics.get_uptime(),
            }
            return (
                200,
                [("Content-Type", "application/json")],
                json.dumps(stats).encode(),
            )

        # Return None to continue with WebSocket handshake
        return None


class HTTPServer:
    """
    Simple HTTP server for health checks and metrics.

    Runs alongside the WebSocket server.
    """

    def __init__(self, ws_server: WebSocketServer):
        self.ws_server = ws_server
        self._server: Optional[websockets.WebSocketServerProtocol] = None

    async def handle_request(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle HTTP request."""
        try:
            # Read request line
            request_line = await reader.readline()
            request = request_line.decode().strip()

            # Read headers
            headers = {}
            while True:
                line = await reader.readline()
                if line == b"\r\n" or line == b"":
                    break
                key, value = line.decode().strip().split(": ", 1)
                headers[key] = value

            # Parse path
            parts = request.split(" ")
            if len(parts) >= 2:
                method, path = parts[0], parts[1]
            else:
                return

            # Handle requests
            if method == "GET" and path == "/health":
                health = await self.ws_server.health_check()
                body = json.dumps(health).encode()
                self._send_response(writer, 200, "OK", body, "application/json")

            elif method == "GET" and path == "/metrics":
                body = self.ws_server.get_metrics().to_prometheus().encode()
                self._send_response(writer, 200, "OK", body, "text/plain")

            else:
                self._send_response(writer, 404, "Not Found", b"Not Found", "text/plain")

        except Exception as e:
            logger.error(f"HTTP error: {e}")
        finally:
            await writer.drain()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    def _send_response(
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

    async def start(self, host: str = "0.0.0.0", port: int = 8766) -> None:
        """Start HTTP server."""
        self._server = await asyncio.start_server(
            self.handle_request,
            host,
            port + 1,  # Use next port for HTTP
        )
        logger.info(f"HTTP server started on http://{host}:{port + 1}")

        async with self._server:
            await self._server.serve_forever()
