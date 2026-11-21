"""Entry point: python main.py (settings are read from .env)."""

import logging
import sys

from dotenv import load_dotenv

from status_bot.bot import build_application
from status_bot.config import ConfigError, Settings


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # These libraries log every HTTP request (including Telegram polling) at INFO.
    for name in ("httpx", "httpcore", "apscheduler"):
        logging.getLogger(name).setLevel(logging.WARNING)

    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        sys.exit(f"Configuration error: {exc}")

    build_application(settings).run_polling()


if __name__ == "__main__":
    main()
