from fastapi import FastAPI
from services.flight_api import get_flights

app = FastAPI()

@app.get("/")
def root():
    return {"message": "Flight Engine läuft 🚀"}

@app.get("/search")
def search(origin: str, destination: str, date: str):
    flights = get_flights(origin, destination, date)
    return flights
