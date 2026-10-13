from datetime import datetime, timedelta

from services.flight_api import NoFlightsError, UpstreamError, get_flights
from services.scoring import get_best_flight

DATE_FORMAT = "%Y-%m-%d"


def simulate_dates(origin, destination, base_date, return_date=None, trip_type="oneway"):
    """Fragt die Nachbartage (±1, ±2) bei SerpApi ab (4 zusaetzliche API-Calls).

    Bei Roundtrip werden Hin- und Rueckflugdatum gemeinsam verschoben.
    Schlaegt ein Tag fehl oder hat keine Fluege, wird er uebersprungen.
    """
    results = []

    base = datetime.strptime(base_date, DATE_FORMAT)
    base_return = datetime.strptime(return_date, DATE_FORMAT) if return_date else None

    for offset in [-2, -1, 1, 2]:
        new_date_str = (base + timedelta(days=offset)).strftime(DATE_FORMAT)
        new_return_str = (
            (base_return + timedelta(days=offset)).strftime(DATE_FORMAT)
            if base_return
            else None
        )

        try:
            flights, _ = get_flights(origin, destination, new_date_str, new_return_str, trip_type)
        except (NoFlightsError, UpstreamError):
            continue

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
