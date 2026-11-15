import logging

import httpx
import pytest

from status_bot.checkers import AachenTerminChecker, CheckError, PageStatus

START = "https://bportal.example/detail/15000/show"
TERMIN = "https://termine.example/auslaenderamt/"

PAGES = {
    "/detail/15000/show": f'<a href="{TERMIN}">Termin buchen</a>',
    "/auslaenderamt/": (
        "<button onclick=\"window.location.href='select2?md=1'\">ABH</button>"
    ),
    "/auslaenderamt/select2": """
        <form action="location" method="get" id="cnc-select-form">
          <input type="hidden" name="mdt" value="95">
          <input type="hidden" name="select_cnc" value="1">
          <input type="number" name="cnc-341" value="0"
                 data-tevis-cncname="Beratungs- und Antragsservice">
          <input type="number" name="cnc-344" value="0"
                 data-tevis-cncname="RWTH Studenten">
        </form>""",
    "/auslaenderamt/location": """
        <form method="post">
          <input type="hidden" name="loc" value="50">
          <input type="submit" id="WeiterButton" name="select_location"
                 value="Weiter">
        </form>""",
}

NO_SLOTS = "<h1>Schritt 4: Terminvorschläge</h1><h2>Kein freier Termin verfügbar</h2>"
SLOTS = "<h1>Schritt 4: Terminvorschläge</h1><button>Mo, 17.11. 09:00</button>"


def make_checker(final_page: str, requests: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, html=final_page)
        return httpx.Response(200, html=PAGES[request.url.path])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return AachenTerminChecker("RWTH Studenten", start_url=START, client=client)


def test_no_slots():
    assert make_checker(NO_SLOTS).check() is PageStatus.TERMIN_UNAVAILABLE


def test_slots_available():
    assert make_checker(SLOTS).check() is PageStatus.TERMIN_AVAILABLE


def test_follows_the_chain():
    requests: list[httpx.Request] = []
    make_checker(NO_SLOTS, requests).check()

    anliegen = requests[3]
    assert anliegen.url.path == "/auslaenderamt/location"
    assert dict(anliegen.url.params) == {"mdt": "95", "select_cnc": "1", "cnc-344": "1"}

    location = requests[4]
    assert location.method == "POST"
    assert location.content == b"loc=50&select_location=Weiter"


def test_anliegen_name_ignores_case_and_spaces():
    checker = make_checker(NO_SLOTS)
    checker.anliegen = "  rwth   studenten "
    assert checker.check() is PageStatus.TERMIN_UNAVAILABLE


def test_unknown_anliegen():
    checker = make_checker(NO_SLOTS)
    checker.anliegen = "Nicht vorhanden"
    with pytest.raises(CheckError, match="RWTH Studenten"):
        checker.check()


def test_unexpected_final_page_is_error():
    with pytest.raises(CheckError, match="Schritt 4"):
        make_checker("<h1>Fehler</h1>").check()


def test_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    checker = AachenTerminChecker("RWTH Studenten", start_url=START, client=client)
    with pytest.raises(CheckError, match="HTTP"):
        checker.check()


@pytest.mark.network
def test_real_site():
    assert isinstance(AachenTerminChecker().check(), PageStatus)


def test_logs_steps_and_result(caplog):
    with caplog.at_level(logging.DEBUG, logger="status_bot"):
        make_checker(NO_SLOTS).check()

    messages = [r.getMessage() for r in caplog.records]
    step2 = "Step 2: selecting Anliegen 'RWTH Studenten'"
    assert any(m.startswith(step2) for m in messages)
    result = caplog.records[-1]
    assert result.levelno == logging.INFO
    assert "RWTH Studenten" in result.getMessage()
    assert "TERMIN_UNAVAILABLE" in result.getMessage()
