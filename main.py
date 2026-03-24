from fastapi import FastAPI
from services.flight_api import get_flights
from services.scoring import get_best_flight
from services.simulation import simulate_dates
from services.decision import make_decision
from typing import Optional

app = FastAPI()


@app.get("/")
def root():
    return {"message": "Flight Engine läuft 🚀"}


@app.get("/search")
def search(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: Optional[str] = None,
    trip_type: str = "oneway"
):
    if trip_type == "roundtrip" and not return_date:
        return {"error": "return_date required for roundtrip"}

    flights, price_history = get_flights(
        origin,
        destination,
        departure_date,
        return_date,
        trip_type
    )

    if not flights:
        return {"error": "no flights found"}

    best, score = get_best_flight(flights)
    cheapest = min(flights, key=lambda x: x["price"])

    if best is None:
        return {"error": "no best flight found"}

    decision_data = make_decision(best["price"], flights, price_history)

    simulations = simulate_dates(origin, destination, departure_date)

    return {
        "best_flight": best,
        "cheapest_flight": cheapest,
        "score": score,
        "decision": decision_data,
        "all_flights": flights,
        "simulations": simulations
    }
