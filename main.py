import re
from datetime import date, datetime
from enum import Enum
from typing import Optional

from fastapi import FastAPI, HTTPException

from services.airports import resolve_airports
from services.decision import make_decision
from services.flight_api import MissingApiKeyError, NoFlightsError, UpstreamError, get_flights
from services.scoring import get_best_flight
from services.simulation import DATE_FORMAT, simulate_dates

app = FastAPI(title="Flight Engine")

AIRPORT_CODE = re.compile(r"^[A-Z]{3}$")


class TripType(str, Enum):
    oneway = "oneway"
    roundtrip = "roundtrip"


def parse_date(value: str, field: str) -> date:
    try:
        return datetime.strptime(value, DATE_FORMAT).date()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field} must be a valid date in YYYY-MM-DD format")


def validate_airports(value: str, field: str) -> None:
    """Prueft nach der Stadt-Aufloesung, dass alle Eintraege 3-Buchstaben-IATA-Codes sind."""
    for code in resolve_airports(value).split(","):
        if not AIRPORT_CODE.match(code):
            raise HTTPException(
                status_code=400,
                detail=f"{field}: '{code}' is not a known city or a 3-letter airport code",
            )


@app.get("/")
def root():
    return {"message": "Flight Engine läuft 🚀"}


@app.get("/search")
def search(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: Optional[str] = None,
    trip_type: TripType = TripType.oneway
):
    validate_airports(origin, "origin")
    validate_airports(destination, "destination")

    departure = parse_date(departure_date, "departure_date")
    if departure < date.today():
        raise HTTPException(status_code=400, detail="departure_date must not be in the past")

    if trip_type == TripType.roundtrip:
        if not return_date:
            raise HTTPException(status_code=400, detail="return_date required for roundtrip")
        if parse_date(return_date, "return_date") < departure:
            raise HTTPException(status_code=400, detail="return_date must not be before departure_date")
    else:
        # Rueckflugdatum bei Oneway wird ignoriert
        return_date = None

    try:
        flights, price_history = get_flights(
            origin,
            destination,
            departure_date,
            return_date,
            trip_type.value
        )
    except MissingApiKeyError:
        raise HTTPException(status_code=503, detail="Server is not configured (SERPAPI_KEY missing)")
    except NoFlightsError:
        raise HTTPException(status_code=404, detail="no flights found")
    except UpstreamError:
        raise HTTPException(status_code=502, detail="flight data provider (SerpApi) failed")

    if not flights:
        raise HTTPException(status_code=404, detail="no flights found")

    best, score = get_best_flight(flights)
    cheapest = min(flights, key=lambda x: x["price"])

    decision_data = make_decision(best["price"], flights, price_history)

    simulations = simulate_dates(origin, destination, departure_date, return_date, trip_type.value)

    return {
        "best_flight": best,
        "cheapest_flight": cheapest,
        "score": score,
        "decision": decision_data,
        "all_flights": flights,
        "simulations": simulations
    }
