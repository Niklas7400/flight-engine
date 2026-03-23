from serpapi import GoogleSearch
import os
from dotenv import load_dotenv

load_dotenv()

def get_flights(origin, destination, date):
    params = {
        "engine": "google_flights",
        "departure_id": origin,
        "arrival_id": destination,
        "outbound_date": date,
        "currency": "EUR",
        "hl": "en",
        "type": 2,
        "api_key": os.getenv("SERPAPI_KEY")
    }

    search = GoogleSearch(params)
    results = search.get_dict()
    print("FULL RESULTS:", results)

    flights = []

    best_flights = results.get("best_flights", [])

    for flight in best_flights:
        raw_price = flight.get("price", 0)

        # 💰 Preis sauber machen
        if isinstance(raw_price, str):
            price = float(raw_price.replace("$", "").replace(",", ""))
        else:
            price = float(raw_price)

        # 🛫 Segmente holen
        segments = flight.get("flights", [])

        stops = max(len(segments) - 1, 0)

        # ⏱ Dauer berechnen (simple)
        duration = len(segments) * 3  # placeholder

        # 👉 NUR gültige Flüge hinzufügen
        if price > 0:
            flights.append({
                "price": price,
                "duration": duration,
                "stops": stops
            })
    print("Parsed flights:", flights)
    return flights
