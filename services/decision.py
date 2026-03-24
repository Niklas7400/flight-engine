def get_low_price_baseline(flights):
    prices = sorted([f["price"] for f in flights])
    cutoff = int(len(prices) * 0.3)

    cheap_prices = prices[:cutoff] if cutoff > 0 else prices

    return sum(cheap_prices) / len(cheap_prices)


def get_historical_average(price_history):
    if not price_history:
        return 0

    prices = [p[1] for p in price_history]
    return sum(prices) / len(prices)

def build_reason(market_score, timing_score):
    reasons = []

    # Market Bewertung
    if market_score > 10:
        reasons.append("cheaper than most current flights")
    elif market_score < -10:
        reasons.append("more expensive than most flights")

    # Timing Bewertung
    if timing_score > 10:
        reasons.append("below historical average")
    elif timing_score < -10:
        reasons.append("above historical average")

    if not reasons:
        return "price is within normal range"

    return " and ".join(reasons)

def make_decision(best_price, flights, price_history):
    prices = [f["price"] for f in flights]

    # 🔥 1. MARKET ANALYSE
    baseline_price = get_low_price_baseline(flights)
    market_score = (baseline_price - best_price) / baseline_price * 100

    # 🔥 2. HISTORICAL ANALYSE
    historical_avg = get_historical_average(price_history)

    if historical_avg > 0:
        timing_score = (historical_avg - best_price) / historical_avg * 100
    else:
        timing_score = 0

    # 🔥 3. KOMBINIERTER SCORE
    final_score = market_score * 0.6 + timing_score * 0.4
    reason = build_reason(market_score, timing_score)

    # 🔥 4. RISIKO
    max_price = max(prices)
    risk = max_price - best_price

    # 🔥 5. ENTSCHEIDUNG
    if final_score > 15:
        decision = "BUY"
    elif final_score > 5:
        decision = "GOOD"
    elif final_score > -5:
        decision = "OK"
    else:
        decision = "WAIT"

    if market_score > 10 and timing_score > 10:
        label = "🔥 Top Deal"
    elif final_score > 10:
        label = "👍 Good Deal"
    elif final_score > 0:
        label = "😐 Fair Price"
    else:
        label = "⚠️ Overpriced"

    return {
        "decision": decision,
        "label": label,
        "reason": reason,
        "final_score": round(final_score, 2),
        "market_score": round(market_score, 2),
        "timing_score": round(timing_score, 2),
        "baseline_price": round(baseline_price, 2),
        "historical_avg": round(historical_avg, 2),
        "risk": risk
    }
