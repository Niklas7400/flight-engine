CITY_AIRPORTS = {
    "frankfurt": "FRA,HHN",
    "london": "LHR,LGW,STN,LTN",
    "bangkok": "BKK,DMK",
    "milan": "MXP,BGY,LIN",
    "barcelona": "BCN,GRO",
    "paris": "CDG,ORY,BVA",
    "new york": "JFK,EWR,LGA",
}

def resolve_airports(input_str: str) -> str:
    parts = input_str.split(",")

    resolved = []

    for p in parts:
        key = p.strip().lower()

        if key in CITY_AIRPORTS:
            resolved.append(CITY_AIRPORTS[key])
        else:
            resolved.append(p.strip().upper())

    return ",".join(resolved)
