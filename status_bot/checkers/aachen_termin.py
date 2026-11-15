"""Проверка свободных терминов в Ausländerbehörde StädteRegion Aachen.

Цепочка переходов (как в браузере):
1. Страница услуги на bportal.staedteregion-aachen.de -> ссылка на сайт записи.
2. Сайт записи (TEVIS), шаг 1 -> кнопка выбора ведомства.
3. Шаг 2 -> выбор Anliegen по названию, отправка формы.
4. Шаг 3 -> выбор места, кнопка «Weiter».
5. Шаг 4 -> если есть надпись «Kein freier Termin verfügbar», записи нет.
"""

import logging
import re
import time
from contextlib import nullcontext
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from status_bot.checkers.base import CheckError, PageChecker, PageStatus

START_URL = (
    "https://bportal.staedteregion-aachen.de"
    "/detail/-/vr-bis-detail/dienstleistung/15000/show"
)
DEFAULT_ANLIEGEN = "RWTH Studenten"
NO_SLOTS_TEXT = "Kein freier Termin verfügbar"

logger = logging.getLogger(__name__)

_SELECT_MDT_RE = re.compile(r"""['"](select2\?md=\d+)['"]""")
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


class AachenTerminChecker(PageChecker):
    """Проверяет, есть ли свободные термины для заданного Anliegen.

    anliegen — название Anliegen так, как оно написано на шаге 2,
    по умолчанию "RWTH Studenten".
    """

    def __init__(
        self,
        anliegen: str = DEFAULT_ANLIEGEN,
        *,
        start_url: str = START_URL,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.anliegen = anliegen
        self.start_url = start_url
        self._client = client
        self._timeout = timeout

    def check(self) -> PageStatus:
        started = time.monotonic()
        logger.debug("Checking Anliegen '%s': start", self.anliegen)
        try:
            status = self._check()
        except CheckError as exc:
            logger.debug("Checking Anliegen '%s': failed: %s", self.anliegen, exc)
            raise
        logger.info(
            "Anliegen '%s': %s (%.1f s)",
            self.anliegen,
            status.name,
            time.monotonic() - started,
        )
        return status

    def _check(self) -> PageStatus:
        try:
            with self._open_client() as client:
                page = self._final_page(client)
        except httpx.HTTPError as exc:
            raise CheckError(f"HTTP error: {exc}") from exc

        logger.debug("Step 4: heading '%s'", _h1_text(page))
        if NO_SLOTS_TEXT in page.get_text(" ", strip=True):
            logger.debug("Step 4: found text '%s'", NO_SLOTS_TEXT)
            return PageStatus.TERMIN_UNAVAILABLE
        # Без надписи считаем, что запись возможна, но только если мы
        # действительно на шаге выбора термина, а не на странице ошибки.
        if not _is_step(page, 4):
            raise CheckError("Did not reach the appointment selection step (Schritt 4)")
        logger.debug("Step 4: text '%s' not found", NO_SLOTS_TEXT)
        return PageStatus.TERMIN_AVAILABLE

    # --- переходы ---------------------------------------------------------

    def _final_page(self, client: httpx.Client) -> BeautifulSoup:
        logger.debug("Opening service page")
        resp = _get(client, self.start_url)
        logger.debug("Following link to appointment site")
        resp = _get(client, _find_termin_link(resp))
        logger.debug("Step 1: selecting authority")
        resp = _get(client, _find_authority_url(resp))
        logger.debug("Step 2: selecting Anliegen '%s'", self.anliegen)
        resp = _get(client, *_build_anliegen_request(resp, self.anliegen))
        logger.debug("Step 3: selecting location, 'Weiter'")
        resp = _post(client, *_build_location_request(resp))
        return _soup(resp)

    def _open_client(self):
        if self._client is not None:
            return nullcontext(self._client)  # чужой клиент не закрываем
        return httpx.Client(
            follow_redirects=True,
            timeout=self._timeout,
            headers={"User-Agent": _USER_AGENT},
        )


# --- разбор страниц ---------------------------------------------------------


def _find_termin_link(resp: httpx.Response) -> str:
    """Ссылка на сайт записи termine.* со страницы услуги."""
    for a in _soup(resp).find_all("a", href=True):
        url = urljoin(str(resp.url), a["href"])
        if urlparse(url).netloc.startswith("termine."):
            return url
    raise CheckError("No link to the appointment site on the service page")


def _find_authority_url(resp: httpx.Response) -> str:
    """Шаг 1: кнопка ведомства, ведёт на select2?md=N."""
    for button in _soup(resp).find_all("button", onclick=True):
        if m := _SELECT_MDT_RE.search(button["onclick"]):
            return urljoin(str(resp.url), m.group(1))
    raise CheckError("Step 1: authority selection button not found")


def _build_anliegen_request(
    resp: httpx.Response, anliegen: str
) -> tuple[str, dict[str, str]]:
    """Шаг 2: форма выбора Anliegen, выбираем один пункт по названию."""
    form = _soup(resp).find("form", id="cnc-select-form")
    if not isinstance(form, Tag):
        raise CheckError("Step 2: Anliegen selection form not found")

    wanted = _normalize(anliegen)
    inputs = form.find_all("input", attrs={"data-tevis-cncname": True})
    names = [_normalize(i["data-tevis-cncname"]) for i in inputs]
    try:
        field = inputs[names.index(wanted)]["name"]
    except ValueError:
        available = ", ".join(i["data-tevis-cncname"].strip() for i in inputs)
        raise CheckError(
            f"Step 2: no Anliegen '{anliegen}'. Available: {available}"
        ) from None

    params = _hidden_fields(form)
    params[field] = "1"
    return urljoin(str(resp.url), form.get("action") or ""), params


def _build_location_request(resp: httpx.Response) -> tuple[str, dict[str, str]]:
    """Шаг 3: форма с кнопкой «Weiter» (выбор места)."""
    soup = _soup(resp)
    button = soup.find("input", id="WeiterButton") or soup.find(
        "input", attrs={"type": "submit", "name": "select_location"}
    )
    form = button.find_parent("form") if isinstance(button, Tag) else None
    if not isinstance(form, Tag):
        raise CheckError("Step 3: location selection form not found")

    data = _hidden_fields(form)
    data[button["name"]] = button.get("value", "")
    return urljoin(str(resp.url), form.get("action") or ""), data


# --- утилиты ----------------------------------------------------------------


def _get(
    client: httpx.Client, url: str, params: dict[str, str] | None = None
) -> httpx.Response:
    resp = client.get(url, params=params)
    logger.debug("GET %s -> %s", resp.url, resp.status_code)
    resp.raise_for_status()
    return resp


def _post(client: httpx.Client, url: str, data: dict[str, str]) -> httpx.Response:
    resp = client.post(url, data=data)
    logger.debug("POST %s %s -> %s %s", url, data, resp.status_code, resp.url)
    resp.raise_for_status()
    return resp


def _soup(resp: httpx.Response) -> BeautifulSoup:
    return BeautifulSoup(resp.text, "html.parser")


def _hidden_fields(form: Tag) -> dict[str, str]:
    return {
        i["name"]: i.get("value", "")
        for i in form.find_all("input", attrs={"type": "hidden", "name": True})
    }


def _normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def _is_step(page: BeautifulSoup, step: int) -> bool:
    return any(
        h1.get_text(" ", strip=True).startswith(f"Schritt {step}")
        for h1 in page.find_all("h1")
    )


def _h1_text(page: BeautifulSoup) -> str:
    h1 = page.find("h1")
    return h1.get_text(" ", strip=True) if h1 else ""
