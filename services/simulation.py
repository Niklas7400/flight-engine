from services.flight_api import get_flights
from services.scoring import get_best_flight

def simulate_dates(origin, destination, base_date):
    results = []

    offsets = [-2, -1, 1, 2]

    for offset in offsets:
        # 👉 erstmal fake (später echte Datum-Logik)
        flights = get_flights(origin, destination, base_date)

        if not flights:
            continue

        best, score = get_best_flight(flights)

        if best:
            results.append({
                "offset": offset,
                "price": best["price"]
            })

    return results
