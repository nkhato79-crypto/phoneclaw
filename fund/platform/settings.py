#!/usr/bin/env python3
"""Config loading for the trading platform layer.

Credentials never live in the config file. The file names an environment
variable and this module reads it, so config.yaml is safe to commit and a leaked
repo does not leak an account.
"""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field
from typing import Any

try:
    import yaml
    _YAML = True
except ImportError:
    _YAML = False
import json


class ConfigError(RuntimeError):
    pass


@dataclass
class Settings:
    login: int | None = None
    server: str | None = None
    password_env: str = "MT5_PASSWORD"
    terminal_path: str | None = None
    risk: dict[str, Any] = field(default_factory=dict)
    signals_file: str = "fund/signals/intents.json"
    audit_log: str = "fund/platform/audit.jsonl"
    poll_seconds: int = 60

    def password(self) -> str | None:
        pw = os.environ.get(self.password_env)
        if self.login and not pw:
            raise ConfigError(
                f"login {self.login} is configured but ${self.password_env} is unset — "
                f"export it in the shell that runs the trader, never in config.yaml"
            )
        return pw

    def mt5_kwargs(self) -> dict[str, Any]:
        """Arguments for MT5Client. With no login, MT5Client attaches to whatever
        account the already-running terminal is signed in to."""
        return {
            "login": self.login,
            "password": self.password(),
            "server": self.server,
            "terminal_path": self.terminal_path,
        }


def load_settings(path: str | pathlib.Path) -> Settings:
    p = pathlib.Path(path)
    if not p.exists():
        raise ConfigError(
            f"no config at {p}. Copy fund/platform/config.example.yaml to "
            f"{p.name} and edit it — the example ships disarmed."
        )

    text = p.read_text()
    if p.suffix in (".yaml", ".yml"):
        if not _YAML:
            raise ConfigError("PyYAML is not installed: pip install pyyaml")
        raw = yaml.safe_load(text) or {}
    else:
        raw = json.loads(text)

    known = {f for f in Settings.__dataclass_fields__}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"unknown config keys: {sorted(unknown)}")

    return Settings(**raw)
