from fastapi import FastAPI
from services.flight_api import get_flights
from services.scoring import get_best_flight

app = FastAPI()

@app.get("/")
def root():
    return {"message": "Flight Engine läuft 🚀"}

@app.get("/search")
def search(origin: str, destination: str, date: str):
    flights = get_flights(origin, destination, date)
    
    best, score = get_best_flight(flights)

    return {
        "best_flight": best,
        "score": score,
        "all_flights": flights
    }
