from serpapi import GoogleSearch
import os
from dotenv import load_dotenv
from services.airports import resolve_airports

load_dotenv()

def get_flights(origin, destination, date):

    origin = resolve_airports(origin)
    destination = resolve_airports(destination)

    params = {
        "engine": "google_flights",
        "departure_id": origin,
        "arrival_id": destination,
        "outbound_date": date,
        "currency": "EUR",
        "hl": "en",
        "type": 2,
        "max": 20,
        "api_key": os.getenv("SERPAPI_KEY")
    }

    search = GoogleSearch(params)
    results = search.get_dict()

    price_insights = results.get("price_insights", {})
    price_history = price_insights.get("price_history", [])

    all_raw = results.get("best_flights", []) + results.get("other_flights", [])

    for flight in all_raw:
        price = flight.get("price")

        segments = flight.get("flights", [])
        if not segments:
            continue

        first = segments[0]
        last = segments[-1]

        airline = first.get("airline", "Unknown")

        route = " → ".join(
            [seg.get("departure_airport", {}).get("id", "") for seg in segments] +
            [last.get("arrival_airport", {}).get("id", "")]
        )

        print(f"{price}€ | {airline} | {route}")

    flights = []

    best_flights = results.get("best_flights", [])
    other_flights = results.get("other_flights", [])

    #🔥 combine both
    all_flights_raw = best_flights + other_flights

    for flight in all_flights_raw:
        raw_price = flight.get("price", 0)

        # 💰 Preis sauber machen
        if isinstance(raw_price, str):
            price = float(raw_price.replace("$", "").replace(",", ""))
        else:
            price = float(raw_price)

        # 🛫 Segmente holen
        segments = flight.get("flights", [])

        if not segments:
            continue

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
            first_segment = segments[0] if segments else {}
            last_segment = segments[-1] if segments else {}

            airline = first_segment.get("airline", "Unknown")

            departure_time = first_segment.get("departure_airport", {}).get("time", "")
            arrival_time = last_segment.get("arrival_airport", {}).get("time", "")

            # Route bauen
            route_airports = [
                seg.get("departure_airport", {}).get("id", "")
                for seg in segments
            ]
            route_airports.append(
                last_segment.get("arrival_airport", {}).get("id", "")
            )

            route = " → ".join(route_airports)

            flights.append({
                "price": price,
                "duration": duration,
                "stops": stops,
                "airline": airline,
                "departure": departure_time.split(" ")[1] if departure_time else "",
                "arrival": arrival_time.split(" ")[1] if arrival_time else "",
                "route": route,
                "booking_token": flight.get("booking_token")
            })

    flights = sorted(flights, key=lambda x: x["price"])
    return flights, price_history
