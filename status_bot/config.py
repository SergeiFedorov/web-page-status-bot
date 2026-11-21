"""Settings loaded from environment variables (see .env.example)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from status_bot.checkers.aachen_termin import DEFAULT_ANLIEGEN


class ConfigError(ValueError):
    """Missing or invalid setting."""


@dataclass(frozen=True)
class Settings:
    bot_token: str
    chat_id: int | None  # None: setup mode, only /chatid works
    check_interval_min: timedelta
    check_interval_max: timedelta
    reminder_interval: timedelta
    anliegen: str = DEFAULT_ANLIEGEN
    db_path: Path = Path("status_bot.sqlite3")
    timezone: ZoneInfo = ZoneInfo("Europe/Berlin")

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        interval_min = _seconds(env, "CHECK_INTERVAL_MIN")
        interval_max = _seconds(env, "CHECK_INTERVAL_MAX")
        if interval_max < interval_min:
            raise ConfigError("CHECK_INTERVAL_MAX must be >= CHECK_INTERVAL_MIN")

        tz_name = env.get("TIMEZONE", "Europe/Berlin")
        try:
            timezone = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigError(f"TIMEZONE: unknown time zone {tz_name!r}") from exc

        return cls(
            bot_token=_required(env, "BOT_TOKEN"),
            chat_id=_int(env, "CHAT_ID") if env.get("CHAT_ID", "").strip() else None,
            check_interval_min=interval_min,
            check_interval_max=interval_max,
            reminder_interval=_seconds(env, "REMINDER_INTERVAL"),
            anliegen=env.get("ANLIEGEN", "").strip() or DEFAULT_ANLIEGEN,
            db_path=Path(env.get("DB_PATH", "").strip() or "status_bot.sqlite3"),
            timezone=timezone,
        )


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is not set")
    return value


def _int(env: Mapping[str, str], name: str) -> int:
    value = _required(env, name)
    try:
        return int(value)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {value!r}") from None


def _seconds(env: Mapping[str, str], name: str) -> timedelta:
    value = _int(env, name)
    if value <= 0:
        raise ConfigError(f"{name} must be > 0 seconds, got {value}")
    return timedelta(seconds=value)
