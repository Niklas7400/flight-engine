# Flight Engine

Flight search & buy-or-wait decision engine built with Python / FastAPI.

It searches flights via [SerpApi's Google Flights engine](https://serpapi.com/google-flights-api), scores them by price, duration and stops, and combines current market prices with historical price data to recommend whether to **buy now** or **wait**.

## Features

- Multi-airport and city-based origin/destination resolution (e.g. `london` → `LHR,LGW,STN,LTN`)
- One-way and round-trip search
- Flight scoring based on price, duration and number of stops
- Buy-or-wait decision engine combining market and historical price analysis with an explainable reason
- Nearby-date price simulation (±2 days) to spot cheaper alternatives

## Requirements

- Python 3.10+
- A [SerpApi](https://serpapi.com/) API key

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install fastapi uvicorn python-dotenv "serpapi"
```

Create a `.env` file in the project root:

```
SERPAPI_KEY=your_serpapi_key
```

## Running

```bash
uvicorn main:app --reload
```

## API

### `GET /search`

| Parameter        | Type   | Required | Description                                  |
|-------------------|--------|----------|------------------------------------------------|
| `origin`          | string | yes      | Origin airport/city code(s)                   |
| `destination`     | string | yes      | Destination airport/city code(s)               |
| `departure_date`  | string | yes      | Departure date (`YYYY-MM-DD`)                  |
| `return_date`     | string | no       | Return date (`YYYY-MM-DD`), required if `trip_type=roundtrip` |
| `trip_type`       | string | no       | `oneway` (default) or `roundtrip`              |

Example:

```bash
curl "http://localhost:8000/search?origin=frankfurt&destination=london&departure_date=2026-07-10"
```

Response includes the best flight, cheapest flight, scoring details, buy-or-wait decision, all matching flights, and nearby-date price simulations.
