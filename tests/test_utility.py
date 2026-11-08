from datetime import date, datetime, timedelta

from services.models import Leg, Preferences, TripOption, TripProfile
from services.utility import apply_utility, explain_choice


def leg(dep=datetime(2027, 3, 5, 10, 0), dur=360, stops=0, origin="FRA", dest="SID"):
    route = [origin, dest] if stops == 0 else [origin, "LIS", dest]
    return Leg(
        origin=origin,
        destination=dest,
        departure=dep,
        arrival=dep + timedelta(minutes=dur),
        duration_min=dur,
        stops=stops,
        airlines=["TP"],
        route=route,
    )


def option(outbound, inbound, price):
    return TripOption(outbound=outbound, inbound=inbound, price=price, source="oneway_combo")


def profile(**prefs):
    return TripProfile(
        name="test",
        origins=["FRA"],
        destinations=["SID"],
        earliest_departure=date(2027, 3, 1),
        latest_return=date(2027, 3, 20),
        nights=10,
        min_nights=8,
        max_nights=12,
        flex_days=1,
        preferences=Preferences(**prefs),
    )


def inbound_default(dep=datetime(2027, 3, 15, 14, 0), dur=360, stops=0):
    return leg(dep=dep, dur=dur, stops=stops, origin="SID", dest="FRA")


def test_bessere_flugzeit_gewinnt_bei_5_eur_mehr_wenn_fruehe_abfluege_stoeren():
    fruh = option(leg(dep=datetime(2027, 3, 5, 5, 40)), inbound_default(), 400)
    gut = option(leg(dep=datetime(2027, 3, 5, 10, 15)), inbound_default(), 405)

    result = apply_utility([fruh, gut], profile())

    assert result[0] is gut
    assert gut.penalties == {}
    assert gut.effective_cost == 405.0
    assert fruh.penalties == {"early_departure_out": 20.0}
    assert fruh.effective_cost == 420.0


def test_rueckflug_einen_tag_spaeter_und_60_eur_billiger_gewinnt_wenn_dauer_egal():
    # Dauer ist egal: lange Rueckreise (13 h) wird nicht bestraft.
    p = profile(avoid_long_duration=False)
    frueh = option(leg(), inbound_default(dep=datetime(2027, 3, 15, 14, 0), dur=360), 1000)
    spaet = option(leg(), inbound_default(dep=datetime(2027, 3, 16, 9, 0), dur=780), 940)

    result = apply_utility([frueh, spaet], p)

    assert result[0] is spaet
    assert spaet.penalties == {}
    assert spaet.effective_cost == 940.0
    assert frueh.effective_cost == 1000.0


def test_flex_tag_ausserhalb_fenster_wird_bestraft_auch_wenn_vorlieben_egal():
    p = profile(
        avoid_early_departure=False,
        avoid_late_arrival=False,
        avoid_stops=False,
        avoid_long_duration=False,
        avoid_length_deviation=False,
    )
    innen = option(leg(dep=datetime(2027, 3, 1, 10, 0)), inbound_default(), 500)
    aussen = option(leg(dep=datetime(2027, 2, 27, 10, 0)), inbound_default(), 480)

    result = apply_utility([aussen, innen], p)

    # 2 Tage vor earliest_departure, Strafe 24 EUR je Tag (5 % von 480) -> 528 > 500
    assert aussen.penalties == {"outside_window": 48.0}
    assert aussen.effective_cost == 528.0
    assert innen.penalties == {}
    assert result[0] is innen


def test_flex_tag_nach_latest_return_wird_bestraft():
    p = profile(avoid_early_departure=False, avoid_late_arrival=False, avoid_stops=False,
                avoid_long_duration=False)
    rueck = option(leg(), inbound_default(dep=datetime(2027, 3, 22, 14, 0)), 500)

    apply_utility([rueck], p)

    assert rueck.penalties == {"outside_window": 50.0}


def test_long_duration_ist_relativ_zur_schnellsten_verbindung():
    schnell = option(leg(dep=datetime(2027, 3, 5, 8, 0), dur=360), inbound_default(), 500)
    mittel = option(leg(dep=datetime(2027, 3, 5, 8, 0), dur=480), inbound_default(), 500)
    lang = option(leg(dep=datetime(2027, 3, 5, 8, 0), dur=780), inbound_default(), 500)

    apply_utility([schnell, mittel, lang], profile())

    assert schnell.penalties == {}
    assert mittel.penalties == {}  # 480 min <= 1.5 * 360 und <= 360 + 180
    assert lang.penalties == {"long_duration_out": 25.0}  # 780 > 540


def test_long_duration_bezieht_sich_auf_die_liste_nicht_auf_feste_werte():
    # Alle Verbindungen sind lang; die schnellste ist 13 h -> keine Strafe.
    a = option(leg(dep=datetime(2027, 3, 5, 8, 0), dur=780), inbound_default(), 500)
    b = option(leg(dep=datetime(2027, 3, 5, 8, 0), dur=780), inbound_default(), 510)

    apply_utility([a, b], profile())

    assert a.penalties == {}
    assert b.penalties == {}


def test_length_deviation_nur_wenn_gewuenscht():
    kurz = option(leg(), inbound_default(dep=datetime(2027, 3, 15, 14, 0)), 400)  # 10 Naechte
    lang = option(leg(), inbound_default(dep=datetime(2027, 3, 17, 14, 0)), 400)  # 12 Naechte

    apply_utility([kurz, lang], profile(avoid_length_deviation=True))
    assert kurz.penalties == {}
    assert lang.penalties == {"length_deviation": 40.0}  # 2 x 20 EUR

    kurz2 = option(leg(), inbound_default(dep=datetime(2027, 3, 15, 14, 0)), 400)
    lang2 = option(leg(), inbound_default(dep=datetime(2027, 3, 17, 14, 0)), 400)
    apply_utility([kurz2, lang2], profile(avoid_length_deviation=False))
    assert "length_deviation" not in lang2.penalties


def test_penalty_ist_fuenf_prozent_bei_teuren_fluegen():
    # 2000 EUR -> 5 % = 100 EUR pro Punkt
    frueh = option(leg(dep=datetime(2027, 3, 5, 5, 40)), inbound_default(), 2000)
    apply_utility([frueh], profile())
    assert frueh.penalties == {"early_departure_out": 100.0}
    assert frueh.effective_cost == 2100.0


def test_sortierung_nach_effective_cost_dann_price_und_leere_liste():
    a = option(leg(dep=datetime(2027, 3, 5, 5, 40)), inbound_default(), 300)  # 320
    b = option(leg(dep=datetime(2027, 3, 5, 10, 0)), inbound_default(), 330)  # 330
    c = option(leg(dep=datetime(2027, 3, 5, 10, 0)), inbound_default(), 325)  # 325

    result = apply_utility([b, a, c], profile())

    assert result == [a, c, b]  # 320 < 325 < 330
    assert apply_utility([], profile()) == []


def test_explain_choice_gleiche_option():
    a = option(leg(), inbound_default(), 400)
    apply_utility([a], profile())
    assert explain_choice(a, a) == "Die beste Option ist auch die günstigste."


def test_explain_choice_text_flugzeit_und_direktflug():
    best = option(leg(dep=datetime(2027, 3, 5, 10, 15)), inbound_default(), 408)
    cheap = option(
        leg(dep=datetime(2027, 3, 5, 5, 40)),
        inbound_default(dep=datetime(2027, 3, 15, 14, 0), stops=1),
        400,
    )
    result = apply_utility([cheap, best], profile())

    assert result[0] is best
    assert explain_choice(best, cheap) == (
        "Für 8 € mehr: Hinflug um 10:15 statt 05:40 und Rückflug direkt statt mit Umstieg."
    )


def test_explain_choice_text_dauer_und_naechte():
    best = option(leg(dep=datetime(2027, 3, 5, 8, 0), dur=360),
                  inbound_default(dep=datetime(2027, 3, 15, 14, 0)), 430)
    cheap = option(leg(dep=datetime(2027, 3, 5, 8, 0), dur=780),
                   inbound_default(dep=datetime(2027, 3, 17, 14, 0)), 400)
    apply_utility([best, cheap], profile(avoid_length_deviation=True))

    text = explain_choice(best, cheap, wanted_nights=10)

    assert text == (
        "Für 30 € mehr: kürzere Hinreisezeit (6 h statt 13 h) und näher an 10 Nächten."
    )
