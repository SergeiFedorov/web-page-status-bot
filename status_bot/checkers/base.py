"""Общий интерфейс проверки страниц."""

from abc import ABC, abstractmethod
from enum import Enum


class PageStatus(Enum):
    """Результат проверки страницы записи."""

    TERMIN_UNAVAILABLE = "termin_unavailable"  # запись недоступна
    TERMIN_AVAILABLE = "termin_available"  # возможна запись


class CheckError(RuntimeError):
    """Не удалось проверить страницу: сеть, ошибка сервера или изменилась вёрстка."""


class PageChecker(ABC):
    """Проверяет страницу и возвращает её статус."""

    @abstractmethod
    def check(self) -> PageStatus:
        """Возвращает текущий статус. При сбое проверки бросает CheckError."""
