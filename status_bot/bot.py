"""Telegram bot: periodic checks, notifications and chat commands."""

import asyncio
import logging
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from telegram import BotCommand, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    JobQueue,
    MessageHandler,
    filters,
)

from status_bot.checkers import AachenTerminChecker, CheckError, PageChecker, PageStatus
from status_bot.checkers.aachen_termin import BOOKING_URL
from status_bot.config import Settings
from status_bot.monitor import Monitor
from status_bot.storage import CheckRecord, Storage

logger = logging.getLogger(__name__)

CHECK_JOB = "check"
HISTORY_SIZE = 10

COMMANDS = [
    BotCommand("on", "включить проверки"),
    BotCommand("off", "выключить проверки"),
    BotCommand("history", f"последние {HISTORY_SIZE} проверок"),
    BotCommand("status", "текущее состояние"),
    BotCommand("help", "список команд"),
]

STATUS_TEXT = {
    PageStatus.TERMIN_AVAILABLE: "🟢 запись есть",
    PageStatus.TERMIN_UNAVAILABLE: "🔴 записи нет",
}


class StatusService:
    """Runs checks and keeps state; knows nothing about Telegram."""

    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        checker: PageChecker,
        *,
        rng: random.Random | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.checker = checker
        self.monitor = Monitor(
            anliegen=settings.anliegen,
            booking_url=BOOKING_URL,
            reminder_interval=settings.reminder_interval,
        )
        self.state = storage.load_state()
        self._rng = rng or random.Random()
        self._clock = clock

    async def run_check(self) -> list[str]:
        """Checks the page once, stores the result, returns messages to send."""
        now = self._clock()
        try:
            status = await asyncio.to_thread(self.checker.check)
        except Exception as exc:
            if not isinstance(exc, CheckError):
                logger.exception("Unexpected error during check")
            error = str(exc) or type(exc).__name__
            self.storage.add_check(CheckRecord(now, None, error))
            messages = self.monitor.on_error(self.state, error)
        else:
            self.storage.add_check(CheckRecord(now, status))
            messages = self.monitor.on_result(self.state, status, now)
        self.storage.save_state(self.state)
        return messages

    def set_enabled(self, enabled: bool) -> None:
        self.state.enabled = enabled
        self.storage.save_state(self.state)

    def next_delay(self) -> timedelta:
        """Random delay in [min, max], so checks are not perfectly regular."""
        low = self.settings.check_interval_min.total_seconds()
        high = self.settings.check_interval_max.total_seconds()
        return timedelta(seconds=self._rng.uniform(low, high))

    # --- texts -------------------------------------------------------------

    def history_text(self) -> str:
        records = self.storage.last_checks(HISTORY_SIZE)
        if not records:
            return "Проверок ещё не было."
        lines = [f"Последние {len(records)} проверок:"]
        for r in records:
            lines.append(f"{self._fmt_time(r.checked_at)} — {_result_text(r)}")
        return "\n".join(lines)

    def status_text(self, next_run: datetime | None) -> str:
        s = self.settings
        last = self.storage.last_checks(1)
        lines = [
            "Проверки: " + ("✅ включены" if self.state.enabled else "⏸ выключены"),
            f"Anliegen: {s.anliegen}",
            "Последняя проверка: "
            + (
                f"{_result_text(last[0])} ({self._fmt_time(last[0].checked_at)})"
                if last
                else "ещё не было"
            ),
            f"Интервал проверки: {_fmt_duration(s.check_interval_min)}"
            f" – {_fmt_duration(s.check_interval_max)}",
            "Напоминание, пока запись есть: раз в "
            + _fmt_duration(s.reminder_interval),
        ]
        if self.state.enabled and next_run:
            lines.append(f"Следующая проверка: {self._fmt_time(next_run)}")
        return "\n".join(lines)

    def _fmt_time(self, value: datetime) -> str:
        return value.astimezone(self.settings.timezone).strftime("%d.%m %H:%M:%S")


def _result_text(record: CheckRecord) -> str:
    if record.status is None:
        error = record.error or ""
        return f"⚠️ ошибка: {error[:100]}"
    return STATUS_TEXT[record.status]


def _fmt_duration(value: timedelta) -> str:
    seconds = int(value.total_seconds())
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{hours} ч" if hours else "", f"{minutes} мин" if minutes else ""]
    parts.append(f"{secs} с" if secs else "")
    return " ".join(p for p in parts if p) or "0 с"


# --- Telegram glue ------------------------------------------------------------


def build_application(settings: Settings) -> Application:
    storage = Storage(settings.db_path)
    service = StatusService(settings, storage, AachenTerminChecker(settings.anliegen))
    chat_id = settings.chat_id
    only_our_chat = filters.Chat(chat_id=chat_id)

    def schedule_check(job_queue: JobQueue, delay: timedelta) -> None:
        for job in job_queue.get_jobs_by_name(CHECK_JOB):
            job.schedule_removal()
        job_queue.run_once(check_job, when=delay, name=CHECK_JOB)
        logger.debug("Next check in %.0f s", delay.total_seconds())

    def next_run(job_queue: JobQueue) -> datetime | None:
        jobs = job_queue.get_jobs_by_name(CHECK_JOB)
        return jobs[0].next_t if jobs else None

    async def check_job(context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            for text in await service.run_check():
                await context.bot.send_message(chat_id, text)
        except TelegramError:
            logger.exception("Failed to send notification")
        finally:
            if service.state.enabled:
                schedule_check(context.job_queue, service.next_delay())

    async def on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if service.state.enabled:
            await update.effective_message.reply_text("Проверки уже включены.")
            return
        service.set_enabled(True)
        schedule_check(context.job_queue, timedelta(0))
        logger.info("Checks enabled by %s", update.effective_user)
        await update.effective_message.reply_text(
            "✅ Проверки включены. Первая проверка — сейчас."
        )

    async def off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        service.set_enabled(False)
        for job in context.job_queue.get_jobs_by_name(CHECK_JOB):
            job.schedule_removal()
        logger.info("Checks disabled by %s", update.effective_user)
        await update.effective_message.reply_text("⏸ Проверки выключены.")

    async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(service.history_text())

    async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = service.status_text(next_run(context.job_queue))
        await update.effective_message.reply_text(text)

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        lines = [f"Слежу за записью «{settings.anliegen}»: {BOOKING_URL}", ""]
        lines += [f"/{c.command} — {c.description}" for c in COMMANDS]
        await update.effective_message.reply_text("\n".join(lines))

    async def chatid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Works in any chat: helps to find CHAT_ID during setup.
        chat = update.effective_chat.id
        await update.effective_message.reply_text(f"CHAT_ID: {chat}")

    async def foreign_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info("Ignoring command from foreign chat %s", update.effective_chat.id)

    async def post_init(app: Application) -> None:
        await app.bot.set_my_commands(COMMANDS)
        logger.info(
            "Bot started: chat %s, Anliegen '%s', checks %s",
            chat_id,
            settings.anliegen,
            "enabled" if service.state.enabled else "disabled",
        )
        if chat_id is None:
            logger.warning(
                "CHAT_ID is not set: setup mode, checks are off. "
                "Send /chatid in the target chat and put the number into .env"
            )
        elif service.state.enabled:
            schedule_check(app.job_queue, timedelta(seconds=5))

    async def post_shutdown(app: Application) -> None:
        storage.close()

    app = (
        Application.builder()
        .token(settings.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("chatid", chatid_cmd))
    for name, handler in [
        ("on", on_cmd),
        ("off", off_cmd),
        ("history", history_cmd),
        ("status", status_cmd),
        ("help", help_cmd),
        ("start", help_cmd),
    ]:
        app.add_handler(CommandHandler(name, handler, filters=only_our_chat))
    app.add_handler(MessageHandler(filters.COMMAND & ~only_our_chat, foreign_chat))
    return app
