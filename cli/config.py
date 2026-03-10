"""
CLI Configuration Module
"""

import os
import yaml
from pathlib import Path
from typing import Optional, Dict, Any


class CLIConfig:
    """Configuration manager for CLI."""

    DEFAULT_CONFIG = {
        "connection": {
            "uri": "ws://localhost:8765",
            "auth_token": None,
            "timeout": 30,
        },
        "ssh": {
            "enabled": False,
            "host": None,
            "remote_port": 8765,
            "local_port": 8765,
            "key": None,
        },
        "ui": {
            "mode": "rich",  # rich, simple
            "refresh_rate": 4,
        },
    }

    def __init__(self, config_path: Optional[str] = None):
        self._config: Dict[str, Any] = self.DEFAULT_CONFIG.copy()

        # Load from file
        if config_path:
            self._load_from_file(config_path)
        else:
            # Try default locations
            self._try_default_locations()

        # Load from env
        self._load_from_env()

    def _load_from_file(self, path: str) -> None:
        """Load from YAML file."""
        config_file = Path(path)
        if config_file.exists():
            with open(config_file, "r") as f:
                file_config = yaml.safe_load(f)
                if file_config:
                    self._merge_config(file_config)

    def _try_default_locations(self) -> None:
        """Try loading from default config locations."""
        default_paths = [
            Path.home() / ".claw-observer" / "config.yaml",
            Path.cwd() / "claw-observer.yaml",
        ]

        for path in default_paths:
            if path.exists():
                self._load_from_file(str(path))
                break

    def _load_from_env(self) -> None:
        """Load from environment variables."""
        env_mapping = {
            "CLAW_OBSERVER_URI": ("connection", "uri"),
            "CLAW_OBSERVER_TOKEN": ("connection", "auth_token"),
            "CLAW_OBSERVER_SSH_HOST": ("ssh", "host"),
            "CLAW_OBSERVER_SSH_PORT": ("ssh", "remote_port"),
            "CLAW_OBSERVER_UI_MODE": ("ui", "mode"),
        }

        for env_var, (section, key) in env_mapping.items():
            value = os.environ.get(env_var)
            if value:
                if section not in self._config:
                    self._config[section] = {}
                self._config[section][key] = value

    def _merge_config(self, new_config: Dict[str, Any]) -> None:
        """Merge configuration."""
        for section, values in new_config.items():
            if isinstance(values, dict):
                if section not in self._config:
                    self._config[section] = {}
                for key, value in values.items():
                    self._config[section][key] = value

    def get(self, section: str, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        return self._config.get(section, {}).get(key, default)

    @property
    def uri(self) -> str:
        return self.get("connection", "uri", "ws://localhost:8765")

    @property
    def auth_token(self) -> Optional[str]:
        return self.get("connection", "auth_token")

    @property
    def ssh_enabled(self) -> bool:
        return bool(self.get("ssh", "enabled", False))

    @property
    def ssh_host(self) -> Optional[str]:
        return self.get("ssh", "host")

    @property
    def ssh_remote_port(self) -> int:
        return int(self.get("ssh", "remote_port", 8765))

    @property
    def ssh_local_port(self) -> int:
        return int(self.get("ssh", "local_port", 8765))

    @property
    def ssh_key(self) -> Optional[str]:
        return self.get("ssh", "key")

    @property
    def ui_mode(self) -> str:
        return self.get("ui", "mode", "rich")


# Global config instance
_config: Optional[CLIConfig] = None


def get_config() -> CLIConfig:
    """Get global configuration."""
    global _config
    if _config is None:
        _config = CLIConfig()
    return _config
