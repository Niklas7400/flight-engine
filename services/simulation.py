from services.flight_api import get_flights
from services.scoring import get_best_flight

from datetime import datetime, timedelta

def simulate_dates(origin, destination, base_date):
    results = []

    base = datetime.strptime(base_date, "%Y-%m-%d")

    offsets = [-2, -1, 1, 2]

    for offset in offsets:
        new_date = base + timedelta(days=offset)
        new_date_str = new_date.strftime("%Y-%m-%d")

        flights, _ = get_flights(origin, destination, new_date_str)

        if not flights:
            continue

        best, _ = get_best_flight(flights)

        if best:
            results.append({
                "offset": offset,
                "date": new_date_str,
                "price": best["price"]
            })

    return results
