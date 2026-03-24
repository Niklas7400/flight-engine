def score_flight(flight):
    score = 0

    # 💰 Preis (wichtigster Faktor)
    score += (1000 - flight["price"]) * 0.6

    # ⏱ Dauer (weniger wichtig als Preis)
    score -= flight["duration"] * 5

    # Extra penalty für sehr lange Flüge (weich!)
    score -= max(0, (flight["duration"] - 15)) * 8

    # 🛑 Stops (wichtig für Komfort)
    score -= flight["stops"] * 50

    # 🟢 Direktflug Bonus
    if flight["stops"] == 0:
        score += 100

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
