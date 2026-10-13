import pytest
from fastapi.testclient import TestClient

import main


class FakeSearch:
    """Ersetzt serpapi.GoogleSearch; liefert die in `response` gesetzte Antwort."""

    response = {}
    calls = []

    def __init__(self, params):
        FakeSearch.calls.append(params)

    def get_dict(self):
        if isinstance(FakeSearch.response, Exception):
            raise FakeSearch.response
        return FakeSearch.response


def segment(dep, arr, airline="LH"):
    return {
        "airline": airline,
        "departure_airport": {"id": dep, "time": "2099-07-10 08:00"},
        "arrival_airport": {"id": arr, "time": "2099-07-10 09:30"},
    }


@pytest.fixture
def serpapi(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "test-key")
    monkeypatch.setattr("services.flight_api.GoogleSearch", FakeSearch)
    FakeSearch.calls = []
    FakeSearch.response = {}
    return FakeSearch


@pytest.fixture
def client():
    return TestClient(main.app)
