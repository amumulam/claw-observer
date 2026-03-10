"""
SSH Tunnel Module

Manages SSH tunnels for connecting to remote Sidecar services.
"""

import asyncio
import logging
import subprocess
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class SSHTunnel:
    """
    Manages SSH tunnel for port forwarding.

    Usage:
        tunnel = SSHTunnel("user@host", 8765)
        await tunnel.start()
        # ... use tunnel ...
        await tunnel.stop()
    """

    def __init__(
        self,
        host: str,
        remote_port: int,
        local_port: Optional[int] = None,
        ssh_key: Optional[str] = None,
    ):
        """
        Initialize SSH tunnel.

        Args:
            host: Remote host (user@hostname)
            remote_port: Remote port to forward
            local_port: Local port (default: same as remote)
            ssh_key: Optional SSH key path
        """
        self.host = host
        self.remote_port = remote_port
        self.local_port = local_port or remote_port
        self.ssh_key = ssh_key

        self._process: Optional[asyncio.subprocess.Process] = None
        self._running = False

    async def start(self) -> bool:
        """
        Start the SSH tunnel.

        Returns:
            True if successful, False otherwise
        """
        # Build SSH command
        cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ServerAliveInterval=60",
            "-o", "ServerAliveCountMax=3",
            "-N",  # No remote command
            "-L", f"{self.local_port}:localhost:{self.remote_port}",
        ]

        if self.ssh_key:
            cmd.extend(["-i", self.ssh_key])

        cmd.append(self.host)

        logger.info(f"Starting SSH tunnel: {' '.join(cmd)}")

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )

            # Wait a bit to check for immediate errors
            await asyncio.sleep(1.0)

            if self._process.returncode is not None:
                # Process exited immediately
                stderr = await self._process.stderr.read()
                logger.error(f"SSH tunnel failed: {stderr.decode()}")
                return False

            self._running = True
            logger.info(f"SSH tunnel established on port {self.local_port}")
            return True

        except FileNotFoundError:
            logger.error("ssh command not found. Is SSH installed?")
            return False
        except Exception as e:
            logger.error(f"Error starting SSH tunnel: {e}")
            return False

    async def stop(self) -> None:
        """Stop the SSH tunnel."""
        if not self._running:
            return

        logger.info("Stopping SSH tunnel...")

        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("SSH tunnel did not terminate gracefully, killing...")
                self._process.kill()
            except Exception as e:
                logger.error(f"Error stopping SSH tunnel: {e}")

        self._running = False
        logger.info("SSH tunnel stopped")

    @property
    def is_running(self) -> bool:
        """Check if tunnel is running."""
        return self._running and self._process and self._process.returncode is None

    @property
    def local_uri(self) -> str:
        """Get the local WebSocket URI."""
        return f"ws://localhost:{self.local_port}"


async def create_tunnel(
    host: str,
    remote_port: int = 8765,
    local_port: Optional[int] = None,
    ssh_key: Optional[str] = None,
) -> Optional[SSHTunnel]:
    """
    Create and start an SSH tunnel.

    Args:
        host: Remote host
        remote_port: Remote port
        local_port: Local port
        ssh_key: SSH key path

    Returns:
        SSHTunnel instance or None if failed
    """
    tunnel = SSHTunnel(host, remote_port, local_port, ssh_key)
    success = await tunnel.start()

    if success:
        return tunnel
    else:
        return None
