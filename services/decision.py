def make_decision(best_price, flights):
    prices = [f["price"] for f in flights]
    
    avg_price = sum(prices) / len(prices)
    min_price = min(prices)
    max_price = max(prices)

    # Deal Score (% unter Durchschnitt)
    deal_score = (avg_price - best_price) / avg_price

    # Risiko (Worst Case wenn Preis steigt)
    risk = max_price - best_price

    # Entscheidung
    if best_price < avg_price * 0.85:
        decision = "BUY"
    elif best_price > avg_price:
        decision = "WAIT"
    else:
        decision = "NEUTRAL"

    return {
        "decision": decision,
        "avg_price": round(avg_price, 2),
        "deal_score": round(deal_score * 100, 2),
        "risk": risk
    }
