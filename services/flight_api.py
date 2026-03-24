from serpapi import GoogleSearch
import os
from dotenv import load_dotenv
from services.airports import resolve_airports

load_dotenv()


def get_flights(origin, destination, departure_date, return_date=None, trip_type="oneway"):

    # 🔍 Multi-Airport Support
    origin = resolve_airports(origin)
    destination = resolve_airports(destination)

    # ⚙️ Params
    params = {
        "engine": "google_flights",
        "departure_id": origin,
        "arrival_id": destination,
        "outbound_date": departure_date,
        "currency": "EUR",
        "hl": "en",
        "api_key": os.getenv("SERPAPI_KEY")
    }

    # ✈️ Trip Type
    if trip_type == "roundtrip":
        if not return_date:
            raise ValueError("return_date required for roundtrip")
        params["type"] = 1
        params["return_date"] = return_date
    else:
        params["type"] = 2

    # 🔎 API Call
    search = GoogleSearch(params)
    results = search.get_dict()

    # 📉 Price History
    price_history = results.get("price_insights", {}).get("price_history", [])

    # ✈️ Flights sammeln
    best_flights = results.get("best_flights", [])
    other_flights = results.get("other_flights", [])
    all_flights_raw = best_flights + other_flights

    print("\n========== RAW FLIGHTS ==========")
    print("Best:", len(best_flights), "| Other:", len(other_flights))

    flights = []
    is_roundtrip = trip_type == "roundtrip"

    for flight in all_flights_raw:
        raw_price = flight.get("price", 0)

        # 💰 Preis
        if isinstance(raw_price, str):
            price = float(raw_price.replace("$", "").replace(",", ""))
        else:
            price = float(raw_price)

        if price <= 0:
            continue

        # ⏱ Dauer
        duration_raw = flight.get("total_duration", 0)

        if isinstance(duration_raw, int):
            duration = duration_raw / 60
        elif isinstance(duration_raw, str):
            hours, minutes = 0, 0

            if "h" in duration_raw:
                parts = duration_raw.split("h")
                hours = int(parts[0].strip())

                if len(parts) > 1 and "m" in parts[1]:
                    minutes = int(parts[1].replace("m", "").strip())

            duration = hours + minutes / 60
        else:
            duration = 0

        # =========================
        # ✈️ ONE WAY
        # =========================
        if not is_roundtrip:
            segments = flight.get("flights", [])
            if not segments:
                continue

            stops = max(len(segments) - 1, 0)

            first = segments[0]
            last = segments[-1]

            airline = first.get("airline", "Unknown")

            departure_time = first.get("departure_airport", {}).get("time", "")
            arrival_time = last.get("arrival_airport", {}).get("time", "")

            route_airports = [
                seg.get("departure_airport", {}).get("id", "")
                for seg in segments
            ]
            route_airports.append(last.get("arrival_airport", {}).get("id", ""))

            route = " → ".join(route_airports)

            print(f"{price}€ | {airline} | {route}")

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

        # =========================
        # 🔁 ROUNDTRIP (FIXED + ONEWAY SUPPORT)
        # =========================
        else:
            legs = flight.get("flights", [])

            if not isinstance(legs, list) or len(legs) == 0:
                continue

            # 🔥 versuchen echten roundtrip zu bauen
            outbound_segments = []
            return_segments = []

            for seg in legs:
                dep = seg.get("departure_airport", {}).get("id", "")
                arr = seg.get("arrival_airport", {}).get("id", "")

                if dep == origin or arr == destination:
                    outbound_segments.append(seg)
                elif dep == destination or arr == origin:
                    return_segments.append(seg)

            # ✅ echter roundtrip
            if outbound_segments and return_segments:
                first_out = outbound_segments[0]
                last_out = outbound_segments[-1]

                first_ret = return_segments[0]
                last_ret = return_segments[-1]

                airline = first_out.get("airline", "Unknown")

                out_dep = first_out.get("departure_airport", {}).get("time", "")
                out_arr = last_out.get("arrival_airport", {}).get("time", "")

                ret_dep = first_ret.get("departure_airport", {}).get("time", "")
                ret_arr = last_ret.get("arrival_airport", {}).get("time", "")

                outbound_stops = max(len(outbound_segments) - 1, 0)
                return_stops = max(len(return_segments) - 1, 0)
                stops = outbound_stops + return_stops

                out_route = " → ".join(
                    [seg.get("departure_airport", {}).get("id", "") for seg in outbound_segments] +
                    [last_out.get("arrival_airport", {}).get("id", "")]
                )

                ret_route = " → ".join(
                    [seg.get("departure_airport", {}).get("id", "") for seg in return_segments] +
                    [last_ret.get("arrival_airport", {}).get("id", "")]
                )

                print(f"{price}€ | {airline} | {out_route} | RETURN: {ret_route}")

                flights.append({
                    "price": price,
                    "duration": duration,
                    "stops": stops,
                    "airline": airline,

                    "outbound": {
                        "departure": out_dep.split(" ")[1] if out_dep else "",
                        "arrival": out_arr.split(" ")[1] if out_arr else "",
                        "route": out_route
                    },

                    "return": {
                        "departure": ret_dep.split(" ")[1] if ret_dep else "",
                        "arrival": ret_arr.split(" ")[1] if ret_arr else "",
                        "route": ret_route
                    },

                    "booking_token": flight.get("booking_token")
                })

            # ⚠️ fallback → one-way trotz roundtrip
            elif isinstance(legs[0], dict):
                segments = legs

                first = segments[0]
                last = segments[-1]

                airline = first.get("airline", "Unknown")

                departure_time = first.get("departure_airport", {}).get("time", "")
                arrival_time = last.get("arrival_airport", {}).get("time", "")

                route = " → ".join(
                    [seg.get("departure_airport", {}).get("id", "") for seg in segments] +
                    [last.get("arrival_airport", {}).get("id", "")]
                )

                print(f"{price}€ | ⚠️ ONEWAY | {route}")

                flights.append({
                    "price": price,
                    "duration": duration,
                    "stops": max(len(segments) - 1, 0),
                    "airline": airline,

                    "outbound": {
                        "departure": departure_time.split(" ")[1] if departure_time else "",
                        "arrival": arrival_time.split(" ")[1] if arrival_time else "",
                        "route": route
                    },

                    "return": None,

                    "booking_token": flight.get("booking_token")
                })

    # 🔽 sortieren
    flights = sorted(flights, key=lambda x: x["price"])

    print("========== PARSED FLIGHTS ==========")
    for f in flights:
        print(f["price"], "|", f.get("route", "ROUNDTRIP"))

    return flights, price_history
