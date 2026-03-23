from fastapi import FastAPI
from services.flight_api import get_flights
from services.scoring import get_best_flight
from services.decision import make_decision

app = FastAPI()

@app.get("/")
def root():
    return {"message": "Flight Engine läuft 🚀"}

@app.get("/search")
def search(origin: str, destination: str, date: str):
    flights = get_flights(origin, destination, date)
    
    best, score = get_best_flight(flights)

    if best is None:
        return {"error": "no best flight found"}

    decision_data = make_decision(best["price"], flights)

    return {
        "best_flight": best,
        "score": score,
        "decision": decision_data,
        "all_flights": flights
    }
