import json
import logging
import os
from datetime import date, datetime
from typing import Optional

from dotenv import load_dotenv
from serpapi import GoogleSearch

from services.airports import resolve_airports
from services.models import Leg, OneWayOffer, PriceInsights, SearchResult, TripOption

load_dotenv()

logger = logging.getLogger(__name__)


class MissingApiKeyError(Exception):
    """SERPAPI_KEY ist nicht gesetzt."""


class UpstreamError(Exception):
    """SerpApi ist nicht erreichbar oder hat einen Fehler gemeldet."""


class NoFlightsError(Exception):
    """SerpApi hat fuer die Suche keine Ergebnisse."""


def get_flights(origin, destination, departure_date, return_date=None, trip_type="oneway"):

    # 🔍 Multi-Airport Support
    origin = resolve_airports(origin)
    destination = resolve_airports(destination)
    origin_codes = set(origin.split(","))
    destination_codes = set(destination.split(","))

    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        raise MissingApiKeyError("SERPAPI_KEY is not set")

    # ⚙️ Params
    params = {
        "engine": "google_flights",
        "departure_id": origin,
        "arrival_id": destination,
        "outbound_date": departure_date,
        "currency": "EUR",
        "hl": "en",
        "api_key": api_key
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
    try:
        results = GoogleSearch(params).get_dict()
    except Exception as exc:
        # Die Exception-Message kann die URL inkl. api_key enthalten -> nicht weiterreichen.
        logger.error("SerpApi request failed: %s", type(exc).__name__)
        raise UpstreamError("SerpApi request failed") from None

    if not isinstance(results, dict):
        raise UpstreamError("Unexpected response from SerpApi")

    error = results.get("error")
    if error:
        if "hasn't returned any results" in str(error):
            raise NoFlightsError(str(error))
        logger.error("SerpApi returned an error: %s", error)
        raise UpstreamError("SerpApi returned an error")

    # 📉 Price History
    price_history = results.get("price_insights", {}).get("price_history", [])

    # ✈️ Flights sammeln
    best_flights = results.get("best_flights", [])
    other_flights = results.get("other_flights", [])
    all_flights_raw = best_flights + other_flights

    logger.debug("raw flights: best=%d other=%d", len(best_flights), len(other_flights))

    flights = []
    is_roundtrip = trip_type == "roundtrip"

    for flight in all_flights_raw:
        raw_price = flight.get("price", 0)

        # 💰 Preis
        try:
            if isinstance(raw_price, str):
                price = float(raw_price.replace("$", "").replace(",", ""))
            else:
                price = float(raw_price)
        except (TypeError, ValueError):
            continue

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

                if dep in origin_codes or arr in destination_codes:
                    outbound_segments.append(seg)
                elif dep in destination_codes or arr in origin_codes:
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

    logger.debug("parsed %d flights", len(flights))

    return flights, price_history


# ---------------------------------------------------------------------------
# v2: Leg-/TripOption-basierte Suche mit Cache und Call-Zaehlung.
# `storage` wird per Duck-Typing genutzt (cache_get/cache_set/record_api_call).
# ---------------------------------------------------------------------------

def _parse_price(raw) -> Optional[float]:
    try:
        if isinstance(raw, str):
            price = float(raw.replace("$", "").replace("\u20ac", "").replace(",", "").strip())
        else:
            price = float(raw)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _parse_dt(value) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None


def _parse_leg(item: dict) -> Optional[Leg]:
    """Baut ein Leg aus einem SerpApi-Flugobjekt (`flights`, `total_duration`, `layovers`)."""
    segments = item.get("flights")
    if not isinstance(segments, list) or not segments:
        return None
    first, last = segments[0], segments[-1]
    dep = _parse_dt((first.get("departure_airport") or {}).get("time"))
    arr = _parse_dt((last.get("arrival_airport") or {}).get("time"))
    if dep is None or arr is None:
        return None

    duration = item.get("total_duration")
    if isinstance(duration, bool) or not isinstance(duration, int):
        duration = 0
        for seg in segments:
            d = seg.get("duration")
            duration += d if isinstance(d, int) else 0
        for lay in item.get("layovers") or []:
            d = lay.get("duration") if isinstance(lay, dict) else None
            duration += d if isinstance(d, int) else 0

    airlines = []
    for seg in segments:
        name = seg.get("airline")
        if name and name not in airlines:
            airlines.append(name)

    route = [(seg.get("departure_airport") or {}).get("id", "") for seg in segments]
    route.append((last.get("arrival_airport") or {}).get("id", ""))

    return Leg(
        origin=route[0],
        destination=route[-1],
        departure=dep,
        arrival=arr,
        duration_min=duration,
        stops=len(segments) - 1,
        airlines=airlines,
        route=route,
    )


def _parse_insights(results: dict) -> PriceInsights:
    pi = results.get("price_insights")
    if not isinstance(pi, dict):
        return PriceInsights()
    low = high = None
    rng = pi.get("typical_price_range")
    if isinstance(rng, (list, tuple)) and len(rng) >= 2:
        low, high = _parse_price(rng[0]), _parse_price(rng[1])
    history = []
    for entry in pi.get("price_history") or []:
        try:
            history.append((int(entry[0]), float(entry[1])))
        except (TypeError, ValueError, IndexError, KeyError):
            continue
    return PriceInsights(
        lowest_price=_parse_price(pi.get("lowest_price")),
        price_level=pi.get("price_level"),
        typical_low=low,
        typical_high=high,
        history=history,
    )


def _all_flights(results: dict) -> list:
    return list(results.get("best_flights") or []) + list(results.get("other_flights") or [])


def _base_params(origins, destinations, out_day: date, trip_type: int) -> dict:
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        raise MissingApiKeyError("SERPAPI_KEY is not set")
    return {
        "engine": "google_flights",
        "departure_id": ",".join(origins),
        "arrival_id": ",".join(destinations),
        "outbound_date": out_day.isoformat(),
        "type": trip_type,
        "currency": "EUR",
        "hl": "en",
        "gl": "de",
        "adults": 1,
        "api_key": api_key,
    }


def _call_serpapi(params: dict, storage, kind: str):
    """Ein SerpApi-Call mit Cache. Gibt (results, from_cache) zurueck.

    kind: "oneway" | "roundtrip" | "return_leg". Nur echte Calls werden gezaehlt.
    """
    key = json.dumps({k: v for k, v in params.items() if k != "api_key"}, sort_keys=True)
    cached = storage.cache_get(key)
    if cached is not None:
        return cached, True

    try:
        results = GoogleSearch(params).get_dict()
    except Exception as exc:
        logger.error("SerpApi request failed: %s", type(exc).__name__)
        raise UpstreamError("SerpApi request failed") from None

    # Der Call wurde abgesetzt -> zaehlen, auch wenn die Antwort ein Fehler ist.
    storage.record_api_call("serpapi", kind)

    if not isinstance(results, dict):
        raise UpstreamError("Unexpected response from SerpApi")
    error = results.get("error")
    if error:
        if "hasn't returned any results" in str(error):
            raise NoFlightsError(str(error))
        logger.error("SerpApi returned an error: %s", error)
        raise UpstreamError("SerpApi returned an error")

    storage.cache_set(key, results)
    return results, False


def search_oneway(origins: list, destinations: list, day: date, storage) -> SearchResult:
    """Einweg-Suche fuer einen Tag, alle Flughaefen in EINEM Call (type=2)."""
    params = _base_params(origins, destinations, day, 2)
    results, from_cache = _call_serpapi(params, storage, "oneway")
    offers = []
    for item in _all_flights(results):
        price = _parse_price(item.get("price"))
        leg = _parse_leg(item)
        if price is None or leg is None:
            continue
        offers.append(OneWayOffer(leg=leg, price=price, booking_token=item.get("booking_token")))
    offers.sort(key=lambda o: o.price)
    return SearchResult(offers=offers, insights=_parse_insights(results), from_cache=from_cache)


def search_roundtrip(origins: list, destinations: list, out_day: date, ret_day: date,
                     storage, max_outbound: int = 3) -> list:
    """Echte Roundtrip-Suche (type=1).

    Echte API-Calls: 1 (Hinflug-Optionen) + bis zu max_outbound (je ein Folge-Call mit
    `departure_token` fuer die Rueckfluege), also maximal 1 + max_outbound; Cache-Treffer
    zaehlen nicht. Ein Folge-Call ohne Ergebnis ueberspringt die Hinflug-Option.
    """
    params = _base_params(origins, destinations, out_day, 1)
    params["return_date"] = ret_day.isoformat()
    results, _ = _call_serpapi(params, storage, "roundtrip")

    candidates = []
    for item in _all_flights(results):
        price = _parse_price(item.get("price"))
        leg = _parse_leg(item)
        token = item.get("departure_token")
        if leg is None or not token:
            continue
        candidates.append((price if price is not None else float("inf"), leg, token, item))
    candidates.sort(key=lambda c: c[0])

    options = []
    for first_price, out_leg, token, item in candidates[:max(max_outbound, 0)]:
        follow = dict(params)
        follow["departure_token"] = token
        try:
            sub, _ = _call_serpapi(follow, storage, "return_leg")
        except NoFlightsError:
            continue
        best = None  # (price, leg, booking_token)
        for ret in _all_flights(sub):
            leg = _parse_leg(ret)
            if leg is None:
                continue
            p = _parse_price(ret.get("price"))
            if best is None or (p if p is not None else float("inf")) < best[0]:
                best = (p if p is not None else float("inf"), leg, ret.get("booking_token"))
        if best is None:
            continue
        total = best[0] if best[0] != float("inf") else first_price
        if total == float("inf"):
            continue
        tokens = [t for t in (best[2], item.get("booking_token")) if t]
        options.append(TripOption(outbound=out_leg, inbound=best[1], price=total,
                                  source="roundtrip", booking_tokens=tokens))
    options.sort(key=lambda o: o.price)
    return options
