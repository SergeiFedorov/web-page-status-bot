# web-page-status-bot

Telegram-бот, который следит за свободными терминами в Ausländerbehörde
StädteRegion Aachen (по умолчанию Anliegen «RWTH Studenten») и пишет в чат:

- 🟢 когда запись появилась, и напоминает, пока она есть;
- 🔴 когда запись закончилась;
- ⚠️ если сайт не удаётся проверить 3 раза подряд.

Проверки идут через случайные интервалы в диапазоне `[CHECK_INTERVAL_MIN, CHECK_INTERVAL_MAX]`.

## Команды

| Команда | Что делает |
|---|---|
| `/on`, `/off` | включить / выключить проверки |
| `/history` | последние 10 проверок |
| `/status` | текущее состояние и настройки |
| `/help` | список команд |
| `/chatid` | показать id чата (работает в любом чате, нужно для настройки) |

Бот выполняет команды только в чате `CHAT_ID`.

## Запуск

1. Создать бота у [@BotFather](https://t.me/BotFather) (`/newbot`) и получить токен.
2. Окружение и зависимости:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
   ```
3. `cp .env.example .env` и вписать `BOT_TOKEN`.
4. Запустить `.venv/bin/python main.py` с пустым `CHAT_ID` (режим настройки:
   работает только `/chatid`, проверок нет).
5. Добавить бота в группу, написать там `/chatid`, вписать число в `CHAT_ID`
   и перезапустить бота.

История и состояние хранятся в `status_bot.sqlite3` и переживают перезапуск.

## Тесты

```bash
.venv/bin/pytest            # офлайн-тесты
.venv/bin/pytest -m network # проверка реального сайта
```
