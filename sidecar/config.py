"""
Sidecar Monitor Configuration

Supports YAML config files and environment variables.
Environment variables take precedence over config file values.
"""

import os
import yaml
from pathlib import Path
from typing import Optional, Dict, Any


class Config:
    """Configuration manager for Sidecar service."""

    DEFAULT_CONFIG = {
        "server": {
            "host": "0.0.0.0",
            "port": 8765,
            "max_connections": 10,
            "ping_interval": 30,
            "ping_timeout": 10,
        },
        "security": {
            "auth_enabled": False,
            "auth_type": "jwt",
            "jwt_secret": "change-me-in-production",
            "jwt_expiry_hours": 24,
            "tls_enabled": False,
            "tls_cert": "",
            "tls_key": "",
        },
        "log_reader": {
            "source": "auto",  # auto, file, docker, journalctl
            "path": "/var/log/openclaw/gateway.log",
            "docker_container": "openclaw-gateway",
            "buffer_size": 1024,
        },
        "monitoring": {
            "health_check_enabled": True,
            "metrics_enabled": True,
            "heartbeat_interval": 30,
        },
        "logging": {
            "level": "INFO",
            "format": "json",  # json or text
        },
    }

    def __init__(self, config_path: Optional[str] = None):
        self._config: Dict[str, Any] = self.DEFAULT_CONFIG.copy()

        if config_path:
            self._load_from_file(config_path)

        self._load_from_env()

    def _load_from_file(self, path: str) -> None:
        """Load configuration from YAML file."""
        config_file = Path(path)
        if config_file.exists():
            with open(config_file, "r") as f:
                file_config = yaml.safe_load(f)
                if file_config:
                    self._merge_config(file_config)

    def _load_from_env(self) -> None:
        """Load configuration from environment variables."""
        env_mapping = {
            "WS_HOST": ("server", "host"),
            "WS_PORT": ("server", "port"),
            "JWT_SECRET": ("security", "jwt_secret"),
            "JWT_EXPIRY_HOURS": ("security", "jwt_expiry_hours"),
            "AUTH_ENABLED": ("security", "auth_enabled"),
            "TLS_ENABLED": ("security", "tls_enabled"),
            "TLS_CERT": ("security", "tls_cert"),
            "TLS_KEY": ("security", "tls_key"),
            "OPENCLAW_LOG_SOURCE": ("log_reader", "source"),
            "OPENCLAW_LOG_PATH": ("log_reader", "path"),
            "OPENCLAW_DOCKER_CONTAINER": ("log_reader", "docker_container"),
            "LOG_LEVEL": ("logging", "level"),
            "HEARTBEAT_INTERVAL": ("monitoring", "heartbeat_interval"),
        }

        for env_var, (section, key) in env_mapping.items():
            value = os.environ.get(env_var)
            if value:
                # Type conversion
                if value.lower() in ("true", "false"):
                    value = value.lower() == "true"
                elif value.isdigit():
                    value = int(value)

                if section not in self._config:
                    self._config[section] = {}
                self._config[section][key] = value

    def _merge_config(self, new_config: Dict[str, Any]) -> None:
        """Merge new configuration with existing config."""
        for section, values in new_config.items():
            if isinstance(values, dict):
                if section not in self._config:
                    self._config[section] = {}
                for key, value in values.items():
                    self._config[section][key] = value
            else:
                self._config[section] = values

    def get(self, section: str, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        return self._config.get(section, {}).get(key, default)

    def get_section(self, section: str) -> Dict[str, Any]:
        """Get entire configuration section."""
        return self._config.get(section, {})

    @property
    def ws_host(self) -> str:
        return self.get("server", "host", "0.0.0.0")

    @property
    def ws_port(self) -> int:
        return int(self.get("server", "port", 8765))

    @property
    def jwt_secret(self) -> str:
        return self.get("security", "jwt_secret", "change-me-in-production")

    @property
    def auth_enabled(self) -> bool:
        return bool(self.get("security", "auth_enabled", False))

    @property
    def log_source(self) -> str:
        return self.get("log_reader", "source", "auto")

    @property
    def log_path(self) -> str:
        return self.get("log_reader", "path", "/var/log/openclaw/gateway.log")

    @property
    def docker_container(self) -> str:
        return self.get("log_reader", "docker_container", "openclaw-gateway")

    @property
    def log_level(self) -> str:
        return self.get("logging", "level", "INFO")


# Global config instance (lazy loaded)
_config: Optional[Config] = None


def get_config(config_path: Optional[str] = None) -> Config:
    """Get global configuration instance."""
    global _config
    if _config is None:
        _config = Config(config_path)
    return _config


def reload_config(config_path: Optional[str] = None) -> Config:
    """Reload configuration from file."""
    global _config
    _config = Config(config_path)
    return _config
