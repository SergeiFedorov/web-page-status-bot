import asyncio
import random
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from status_bot.bot import StatusService
from status_bot.checkers import CheckError, PageChecker, PageStatus
from status_bot.config import ConfigError, Settings
from status_bot.storage import Storage

T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


class FakeChecker(PageChecker):
    def __init__(self, results):
        self.results = list(results)

    def check(self) -> PageStatus:
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def make_settings(**overrides) -> Settings:
    values = dict(
        bot_token="token",
        chat_id=-100,
        check_interval_min=timedelta(minutes=5),
        check_interval_max=timedelta(minutes=10),
        reminder_interval=timedelta(hours=1),
        timezone=ZoneInfo("Europe/Berlin"),
    )
    return Settings(**(values | overrides))


def make_service(tmp_path, results, settings=None):
    times = iter(T0 + timedelta(minutes=10 * i) for i in range(100))
    return StatusService(
        settings or make_settings(),
        Storage(tmp_path / "db.sqlite3"),
        FakeChecker(results),
        rng=random.Random(1),
        clock=lambda: next(times),
    )


def run_checks(service, n):
    return [asyncio.run(service.run_check()) for _ in range(n)]


def test_history_and_messages(tmp_path):
    service = make_service(
        tmp_path,
        [
            PageStatus.TERMIN_UNAVAILABLE,
            CheckError("HTTP error"),
            PageStatus.TERMIN_AVAILABLE,
        ],
    )
    msgs = run_checks(service, 3)
    assert msgs[0] == [] and msgs[1] == []
    assert "Появилась запись" in msgs[2][0]

    history = service.history_text().splitlines()
    assert history[0] == "Последние 3 проверок:"
    assert history[1] == "16.11 13:20:00 — 🟢 запись есть"  # newest first, Berlin time
    assert history[2] == "16.11 13:10:00 — ⚠️ ошибка: HTTP error"
    assert history[3] == "16.11 13:00:00 — 🔴 записи нет"


def test_history_is_limited_to_10(tmp_path):
    service = make_service(tmp_path, [PageStatus.TERMIN_UNAVAILABLE] * 12)
    run_checks(service, 12)
    assert len(service.history_text().splitlines()) == 1 + 10


def test_unexpected_exception_counts_as_error(tmp_path):
    service = make_service(tmp_path, [ValueError("bug")] * 3)
    msgs = run_checks(service, 3)
    assert "Не удаётся проверить сайт" in msgs[2][0]


def test_state_survives_restart(tmp_path):
    service = make_service(tmp_path, [PageStatus.TERMIN_AVAILABLE])
    run_checks(service, 1)
    service.set_enabled(False)
    service.storage.close()

    restarted = make_service(tmp_path, [PageStatus.TERMIN_AVAILABLE])
    assert restarted.state.enabled is False
    assert restarted.state.last_status is PageStatus.TERMIN_AVAILABLE
    # Same status right after restart: no duplicate "появилась" message.
    assert run_checks(restarted, 1) == [[]]


def test_next_delay_is_random_within_bounds(tmp_path):
    service = make_service(tmp_path, [])
    delays = {service.next_delay() for _ in range(50)}
    assert all(timedelta(minutes=5) <= d <= timedelta(minutes=10) for d in delays)
    assert len(delays) > 1


def test_status_text(tmp_path):
    service = make_service(tmp_path, [PageStatus.TERMIN_UNAVAILABLE])
    run_checks(service, 1)
    text = service.status_text(next_run=T0 + timedelta(minutes=7))
    assert "✅ включены" in text
    assert "🔴 записи нет (16.11 13:00:00)" in text
    assert "5 мин – 10 мин" in text
    assert "раз в 1 ч" in text
    assert "Следующая проверка: 16.11 13:07:00" in text


ENV = {
    "BOT_TOKEN": "token",
    "CHAT_ID": "-1001234",
    "CHECK_INTERVAL_MIN": "300",
    "CHECK_INTERVAL_MAX": "600",
    "REMINDER_INTERVAL": "3600",
}


def test_settings_from_env():
    s = Settings.from_env(ENV)
    assert s.chat_id == -1001234
    assert s.check_interval_max == timedelta(minutes=10)
    assert s.anliegen == "RWTH Studenten"


def test_settings_without_chat_id_is_setup_mode():
    assert Settings.from_env(ENV | {"CHAT_ID": ""}).chat_id is None


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"BOT_TOKEN": ""}, "BOT_TOKEN is not set"),
        ({"CHAT_ID": "abc"}, "CHAT_ID must be an integer"),
        ({"CHECK_INTERVAL_MIN": "0"}, "must be > 0"),
        ({"CHECK_INTERVAL_MAX": "100"}, "CHECK_INTERVAL_MAX must be >="),
        ({"TIMEZONE": "Mars/Base"}, "unknown time zone"),
    ],
)
def test_settings_errors(overrides, error):
    with pytest.raises(ConfigError, match=error):
        Settings.from_env(ENV | overrides)
