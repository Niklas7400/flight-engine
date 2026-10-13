from tests.conftest import segment

BASE = {"origin": "FRA", "destination": "LHR", "departure_date": "2099-07-10"}

ONEWAY_RESPONSE = {
    "best_flights": [{"price": 120, "total_duration": 90, "flights": [segment("FRA", "LHR")]}],
    "other_flights": [
        {"price": 100, "total_duration": 200, "flights": [segment("FRA", "AMS"), segment("AMS", "LHR")]},
        {"price": 300, "total_duration": 95, "flights": [segment("FRA", "LHR")]},
    ],
    "price_insights": {"price_history": [[1, 200], [2, 220]]},
}


def test_oneway_search_ok(client, serpapi):
    serpapi.response = ONEWAY_RESPONSE
    r = client.get("/search", params=BASE)
    assert r.status_code == 200
    body = r.json()
    assert body["cheapest_flight"]["price"] == 100
    assert body["best_flight"]["price"] == 120  # Direktflug gewinnt trotz hoeherem Preis
    assert body["decision"]["historical_avg"] == 210
    assert len(body["simulations"]) == 4
    assert len(serpapi.calls) == 5  # 1 Hauptsuche + 4 Nachbartage


def test_invalid_date_format(client, serpapi):
    r = client.get("/search", params={**BASE, "departure_date": "10.07.2099"})
    assert r.status_code == 400


def test_date_in_past(client, serpapi):
    r = client.get("/search", params={**BASE, "departure_date": "2000-01-01"})
    assert r.status_code == 400


def test_invalid_airport(client, serpapi):
    r = client.get("/search", params={**BASE, "origin": "berlinn"})
    assert r.status_code == 400
    assert serpapi.calls == []


def test_city_names_resolve(client, serpapi):
    serpapi.response = ONEWAY_RESPONSE
    r = client.get("/search", params={**BASE, "origin": "frankfurt", "destination": "london"})
    assert r.status_code == 200
    assert serpapi.calls[0]["departure_id"] == "FRA,HHN"
    assert serpapi.calls[0]["arrival_id"] == "LHR,LGW,STN,LTN"


def test_invalid_trip_type(client, serpapi):
    r = client.get("/search", params={**BASE, "trip_type": "multicity"})
    assert r.status_code == 422  # Enum-Validierung durch FastAPI


def test_roundtrip_without_return_date(client, serpapi):
    r = client.get("/search", params={**BASE, "trip_type": "roundtrip"})
    assert r.status_code == 400
    assert serpapi.calls == []


def test_return_before_departure(client, serpapi):
    r = client.get("/search", params={**BASE, "trip_type": "roundtrip", "return_date": "2099-07-01"})
    assert r.status_code == 400


def test_roundtrip_parsing_and_shifted_simulation(client, serpapi):
    serpapi.response = {
        "best_flights": [
            {"price": 150, "total_duration": 600, "flights": [segment("FRA", "LHR"), segment("LHR", "FRA")]}
        ]
    }
    r = client.get(
        "/search",
        params={**BASE, "trip_type": "roundtrip", "return_date": "2099-07-15"},
    )
    assert r.status_code == 200
    flight = r.json()["best_flight"]
    assert flight["outbound"]["route"] == "FRA → LHR"
    assert flight["return"]["route"] == "LHR → FRA"
    # Simulation verschiebt auch das Rueckflugdatum
    assert serpapi.calls[1]["outbound_date"] == "2099-07-08"
    assert serpapi.calls[1]["return_date"] == "2099-07-13"


def test_roundtrip_with_multi_airport_city(client, serpapi):
    serpapi.response = {
        "best_flights": [
            {"price": 150, "total_duration": 600, "flights": [segment("FRA", "LHR"), segment("LHR", "FRA")]}
        ]
    }
    r = client.get(
        "/search",
        params={**BASE, "origin": "frankfurt", "destination": "london",
                "trip_type": "roundtrip", "return_date": "2099-07-15"},
    )
    assert r.json()["best_flight"]["return"] is not None


def test_no_flights_returns_404(client, serpapi):
    serpapi.response = {"best_flights": [], "other_flights": []}
    assert client.get("/search", params=BASE).status_code == 404


def test_serpapi_no_results_error_returns_404(client, serpapi):
    serpapi.response = {"error": "Google Flights hasn't returned any results for this query."}
    assert client.get("/search", params=BASE).status_code == 404


def test_serpapi_error_returns_502(client, serpapi):
    serpapi.response = {"error": "Invalid API key. Your API key should be here: https://serpapi.com/manage-api-key"}
    assert client.get("/search", params=BASE).status_code == 502


def test_serpapi_exception_returns_502_without_leaking_key(client, serpapi):
    serpapi.response = RuntimeError("HTTPSConnectionPool ... api_key=test-key")
    r = client.get("/search", params=BASE)
    assert r.status_code == 502
    assert "test-key" not in r.text


def test_missing_api_key_returns_503(client, serpapi, monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY")
    assert client.get("/search", params=BASE).status_code == 503


def test_failed_simulation_day_is_skipped(client, serpapi, monkeypatch):
    serpapi.response = ONEWAY_RESPONSE
    original = serpapi.get_dict
    state = {"n": 0}

    def flaky(self):
        state["n"] += 1
        if state["n"] in (2, 3):
            raise RuntimeError("boom")
        return original(self)

    monkeypatch.setattr(serpapi, "get_dict", flaky)
    r = client.get("/search", params=BASE)
    assert r.status_code == 200
    assert len(r.json()["simulations"]) == 2
