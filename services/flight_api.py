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
        duration_raw = flight.get("total_duration", 0)

        # ✅ FALL 1: int (z.B. 850 Minuten)
        if isinstance(duration_raw, int):
            duration = duration_raw / 60  # Minuten → Stunden

        # ✅ FALL 2: string ("12h 30m")
        elif isinstance(duration_raw, str):
            hours = 0
            minutes = 0

            if "h" in duration_raw:
                parts = duration_raw.split("h")
                hours = int(parts[0].strip())

                if len(parts) > 1 and "m" in parts[1]:
                    minutes = int(parts[1].replace("m", "").strip())

            duration = hours + minutes / 60

        # ❌ fallback
        else:
            duration = 0

        # 👉 NUR gültige Flüge hinzufügen
        if price > 0:
            flights.append({
                "price": price,
                "duration": duration,
                "stops": stops
            })
    print("Parsed flights:", flights)
    return flights
