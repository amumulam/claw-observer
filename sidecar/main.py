"""
OpenClaw Monitor Sidecar

Main entry point for the Sidecar service.
Coordinates log reading, parsing, and WebSocket pushing.
"""

import asyncio
import logging
import signal
import sys
from typing import Optional

from .config import get_config, Config
from .log_reader import create_log_reader, LogReader
from .parser import LogParser
from .ws_server import WebSocketServer
from .rules.base import Event

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class Sidecar:
    """
    Main Sidecar service coordinator.

    Orchestrates:
    - Log reading from various sources
    - Log parsing and state machine
    - WebSocket event pushing
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()

        # Initialize components
        self._log_reader: Optional[LogReader] = None
        self._parser = LogParser()
        self._ws_server = WebSocketServer(
            host=self.config.ws_host,
            port=self.config.ws_port,
            auth_enabled=self.config.auth_enabled,
            jwt_secret=self.config.jwt_secret,
        )

        # Connect parser to WebSocket
        self._parser.on_event(self._ws_server.on_parser_event)

        # State
        self._running = False
        self._shutdown_event = asyncio.Event()

        # Register signal handlers
        self._setup_signal_handlers()

    def _setup_signal_handlers(self) -> None:
        """Register signal handlers for graceful shutdown."""
        loop = asyncio.get_event_loop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda: asyncio.create_task(self._shutdown()),
            )

    async def _shutdown(self) -> None:
        """Initiate graceful shutdown."""
        if not self._running:
            return

        logger.info("Shutting down Sidecar...")
        self._running = False

        # Close log reader
        if self._log_reader:
            await self._log_reader.close()

        # Stop WebSocket server
        await self._ws_server.stop()

        # Signal shutdown complete
        self._shutdown_event.set()
        logger.info("Sidecar shutdown complete")

    async def run(self) -> None:
        """
        Run the Sidecar service.

        This is the main entry point.
        """
        self._running = True
        logger.info(f"Starting Sidecar service on ws://{self.config.ws_host}:{self.config.ws_port}")
        logger.info(f"Log source: {self.config.log_source}")

        # Create log reader
        self._log_reader = create_log_reader(
            source=self.config.log_source,
            log_path=self.config.log_path,
            docker_container=self.config.docker_container,
        )

        # Start WebSocket server in background
        ws_task = asyncio.create_task(self._ws_server.start())

        # Start processing logs
        try:
            await self._process_logs()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in Sidecar: {e}")
            raise
        finally:
            # Cleanup
            ws_task.cancel()
            try:
                await ws_task
            except asyncio.CancelledError:
                pass

        # Wait for shutdown to complete
        await self._shutdown_event.wait()

    async def _process_logs(self) -> None:
        """Process log lines from the reader."""
        logger.info("Starting log processing...")

        try:
            async for line in self._log_reader.read_lines():
                if not self._running:
                    break

                # Parse the line
                self._parser.parse_line(line)

                # Log stats periodically
                stats = self._parser.stats
                if stats["lines_processed"] % 1000 == 0:
                    logger.info(
                        f"Processed {stats['lines_processed']} lines, "
                        f"matched {stats['lines_matched']}, "
                        f"current state: {stats['current_state']}"
                    )

        except Exception as e:
            logger.error(f"Error processing logs: {e}")
            raise

    def get_status(self) -> dict:
        """Get current Sidecar status."""
        return {
            "running": self._running,
            "state": self._parser.current_state,
            "clients": len(self._ws_server._clients),
            "stats": self._parser.stats,
        }


async def main() -> None:
    """Main entry point."""
    # Load configuration
    config = get_config()

    # Override log level from config
    log_level = getattr(logging, config.log_level.upper(), logging.INFO)
    logging.getLogger().setLevel(log_level)

    # Create and run Sidecar
    sidecar = Sidecar(config)

    try:
        await sidecar.run()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Sidecar error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
