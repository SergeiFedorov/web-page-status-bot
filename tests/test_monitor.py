from datetime import UTC, datetime, timedelta

from status_bot.checkers import PageStatus
from status_bot.monitor import Monitor
from status_bot.storage import State

AVAILABLE = PageStatus.TERMIN_AVAILABLE
UNAVAILABLE = PageStatus.TERMIN_UNAVAILABLE
T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)

monitor = Monitor(
    anliegen="RWTH Studenten",
    booking_url="https://example/",
    reminder_interval=timedelta(hours=1),
)


def test_first_check_unavailable_is_quiet():
    assert monitor.on_result(State(), UNAVAILABLE, T0) == []


def test_first_check_available_notifies():
    [msg] = monitor.on_result(State(), AVAILABLE, T0)
    assert "Появилась запись" in msg and "https://example/" in msg


def test_status_changes():
    state = State(last_status=UNAVAILABLE)
    [msg] = monitor.on_result(state, AVAILABLE, T0)
    assert "Появилась" in msg
    [msg] = monitor.on_result(state, UNAVAILABLE, T0 + timedelta(minutes=5))
    assert "закончилась" in msg


def test_unchanged_unavailable_is_quiet():
    state = State(last_status=UNAVAILABLE)
    assert monitor.on_result(state, UNAVAILABLE, T0) == []


def test_reminder_while_available():
    state = State(last_status=UNAVAILABLE)
    monitor.on_result(state, AVAILABLE, T0)

    assert monitor.on_result(state, AVAILABLE, T0 + timedelta(minutes=59)) == []
    [msg] = monitor.on_result(state, AVAILABLE, T0 + timedelta(minutes=60))
    assert "всё ещё есть" in msg
    # The next reminder is counted from the last one.
    assert monitor.on_result(state, AVAILABLE, T0 + timedelta(minutes=90)) == []
    assert monitor.on_result(state, AVAILABLE, T0 + timedelta(minutes=120))


def test_error_notifies_once_on_third_in_a_row():
    state = State(last_status=UNAVAILABLE)
    assert monitor.on_error(state, "boom") == []
    assert monitor.on_error(state, "boom") == []
    [msg] = monitor.on_error(state, "boom")
    assert "3 раза подряд" in msg and "boom" in msg
    assert monitor.on_error(state, "boom") == []


def test_recovery_after_errors():
    state = State(last_status=UNAVAILABLE)
    for _ in range(3):
        monitor.on_error(state, "boom")
    [msg] = monitor.on_result(state, UNAVAILABLE, T0)
    assert "снова работает" in msg
    assert state.consecutive_errors == 0


def test_errors_below_threshold_are_silent_and_keep_status():
    state = State(last_status=AVAILABLE, last_available_notice_at=T0)
    monitor.on_error(state, "boom")
    monitor.on_error(state, "boom")
    # Error is not a status change: no "появилась" after recovery.
    assert monitor.on_result(state, AVAILABLE, T0 + timedelta(minutes=10)) == []
