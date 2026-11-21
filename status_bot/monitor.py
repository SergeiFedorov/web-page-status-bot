"""Notification logic: decides which messages to send after each check.

Pure logic without Telegram or network, so it is easy to test.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from status_bot.checkers.base import PageStatus
from status_bot.storage import State

ERROR_THRESHOLD = 3


@dataclass(frozen=True)
class Monitor:
    anliegen: str
    booking_url: str
    reminder_interval: timedelta
    error_threshold: int = ERROR_THRESHOLD

    def on_result(self, state: State, status: PageStatus, now: datetime) -> list[str]:
        """Updates state after a successful check, returns messages to send."""
        messages = []
        if state.consecutive_errors >= self.error_threshold:
            messages.append("✅ Проверка сайта снова работает.")
        state.consecutive_errors = 0

        previous, state.last_status = state.last_status, status
        available = status is PageStatus.TERMIN_AVAILABLE

        if status != previous:
            if available:
                messages.append(
                    f"🟢 Появилась запись для «{self.anliegen}»!\n{self.booking_url}"
                )
                state.last_available_notice_at = now
            elif previous is not None:  # after a restart with empty state stay quiet
                messages.append(f"🔴 Запись для «{self.anliegen}» закончилась.")
        elif available and self._reminder_due(state, now):
            messages.append(
                f"🟢 Запись для «{self.anliegen}» всё ещё есть.\n{self.booking_url}"
            )
            state.last_available_notice_at = now
        return messages

    def on_error(self, state: State, error: str) -> list[str]:
        """Updates state after a failed check, returns messages to send.

        The last known status is kept: a failure is not a status change.
        """
        state.consecutive_errors += 1
        if state.consecutive_errors == self.error_threshold:
            return [
                f"⚠️ Не удаётся проверить сайт {self.error_threshold} раза подряд.\n"
                f"Последняя ошибка: {error}"
            ]
        return []

    def _reminder_due(self, state: State, now: datetime) -> bool:
        last = state.last_available_notice_at
        return last is None or now - last >= self.reminder_interval
