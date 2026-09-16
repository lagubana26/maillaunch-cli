"""
Configuration loading for MailLaunch.

Reads maillaunch.config.yaml (non-sensitive defaults) and layers in
sensitive values from environment variables. Nothing sensitive (tokens,
client secrets loaded at runtime) is ever written back to the YAML file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

DEFAULT_CONFIG_PATHS = [
    Path("maillaunch.config.yaml"),
    Path.home() / ".config" / "maillaunch" / "config.yaml",
]


@dataclass
class SendConfig:
    min_delay_seconds: int = 60
    max_delay_seconds: int = 180
    retry_failed: bool = True
    retry_attempts: int = 2


@dataclass
class ProviderConfig:
    client_id: Optional[str] = None
    # client_secret is intentionally NOT loaded from YAML. It is read
    # from an environment variable (see Config.gmail_client_secret) or
    # prompted for interactively during `auth gmail`.


@dataclass
class Config:
    provider: str = "gmail"
    daily_limit: int = 100
    gmail: ProviderConfig = field(default_factory=ProviderConfig)
    microsoft: ProviderConfig = field(default_factory=ProviderConfig)
    send: SendConfig = field(default_factory=SendConfig)
    config_path: Optional[Path] = None

    @property
    def gmail_client_secret(self) -> Optional[str]:
        # Never stored in the YAML file. Pulled from env var or the
        # OS credential store at auth time.
        return os.environ.get("MAILLAUNCH_GMAIL_CLIENT_SECRET")

    @property
    def microsoft_client_secret(self) -> Optional[str]:
        # Public-client device-code flow typically needs no secret;
        # provided for completeness if a confidential app is used.
        return os.environ.get("MAILLAUNCH_MS_CLIENT_SECRET")


def find_config_file(explicit_path: Optional[str] = None) -> Optional[Path]:
    if explicit_path:
        p = Path(explicit_path)
        return p if p.exists() else None
    for candidate in DEFAULT_CONFIG_PATHS:
        if candidate.exists():
            return candidate
    return None


def load_config(explicit_path: Optional[str] = None) -> Config:
    path = find_config_file(explicit_path)
    if path is None:
        # No config file yet -- return defaults; CLI flags / env vars
        # can still fully drive the tool.
        return Config()

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    send_raw = raw.get("send", {}) or {}
    gmail_raw = raw.get("gmail", {}) or {}
    ms_raw = raw.get("microsoft", {}) or {}

    cfg = Config(
        provider=raw.get("provider", "gmail"),
        daily_limit=int(raw.get("daily_limit", 100)),
        gmail=ProviderConfig(client_id=gmail_raw.get("client_id")),
        microsoft=ProviderConfig(client_id=ms_raw.get("client_id")),
        send=SendConfig(
            min_delay_seconds=int(send_raw.get("min_delay_seconds", 60)),
            max_delay_seconds=int(send_raw.get("max_delay_seconds", 180)),
            retry_failed=bool(send_raw.get("retry_failed", True)),
            retry_attempts=int(send_raw.get("retry_attempts", 2)),
        ),
        config_path=path,
    )

    # Basic sanity check -- fail loudly rather than silently misbehave.
    if "client_secret" in gmail_raw or "client_secret" in ms_raw:
        raise ValueError(
            "Refusing to load config: found a client_secret in "
            f"{path}. Secrets must not be stored in the config file -- "
            "use the MAILLAUNCH_GMAIL_CLIENT_SECRET environment "
            "variable instead, or run `maillaunch auth gmail` which "
            "will prompt for it and store it in the OS keychain."
        )

    return cfg
