def score_flight(flight):
    score = 0

    # Preis (je günstiger desto besser)
    score += (1000 - flight["price"]) * 0.5

    # Dauer (kürzer = besser)
    score += (20 - flight["duration"]) * 10

    # Stops (weniger = besser)
    score -= flight["stops"] * 50

    return score

def get_best_flight(flights):
    best = None
    best_score = -999999

    for f in flights:
        score = score_flight(f)
        if score > best_score:
            best_score = score
            best = f

    return best, best_score
