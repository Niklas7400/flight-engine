from datetime import date, datetime
from pathlib import Path

import pytest
import yaml

from services.airports import resolve_airports
from services.models import Preferences, TripProfile
from services.trip_profile import load_profile, profile_from_dict, profile_to_dict

YAML_PATH = Path(__file__).resolve().parents[1] / "trips" / "kapverden_2027.yaml"


def valid():
    return {
        "name": "test_trip",
        "origins": ["FRA"],
        "destinations": ["LIS"],
        "earliest_departure": "2027-03-19",
        "latest_return": "2027-04-15",
        "nights": 10,
        "min_nights": 8,
        "max_nights": 12,
        "flex_days": 1,
    }


def write_yaml(tmp_path, text):
    path = tmp_path / "profile.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


# --- Laden der YAML -------------------------------------------------------

def test_load_kapverden_profile():
    p = load_profile(str(YAML_PATH))
    assert p.name == "kapverden_2027"
    assert p.origins == ["FRA"]
    assert p.destinations == ["SID", "BVC", "RAI"]
    assert p.earliest_departure == date(2027, 3, 19)
    assert p.latest_return == date(2027, 4, 15)
    assert (p.nights, p.min_nights, p.max_nights, p.flex_days) == (10, 8, 12, 1)
    assert p.preferences == Preferences(
        avoid_early_departure=True,
        avoid_late_arrival=True,
        avoid_stops=True,
        avoid_long_duration=True,
        avoid_length_deviation=False,
    )


def test_load_profile_from_yaml_with_defaults(tmp_path):
    path = write_yaml(
        tmp_path,
        "name: min\norigins: FRA\ndestinations: LIS\n"
        "earliest_departure: 2027-01-01\nlatest_return: 2027-01-20\n",
    )
    p = load_profile(path)
    assert p.nights == 10 and p.min_nights == 8 and p.max_nights == 12
    assert p.flex_days == 1 and p.allow_open_jaw is False and p.currency == "EUR"
    assert p.preferences == Preferences()


def test_load_profile_invalid_yaml_syntax(tmp_path):
    with pytest.raises(ValueError, match="YAML-Fehler"):
        load_profile(write_yaml(tmp_path, "name: [unclosed\n"))


def test_load_profile_empty_file(tmp_path):
    with pytest.raises(ValueError, match="Mapping"):
        load_profile(write_yaml(tmp_path, ""))


def test_load_profile_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_profile(str(tmp_path / "gibt_es_nicht.yaml"))


# --- Round-Trip -----------------------------------------------------------

def test_round_trip_loaded_profile():
    p = load_profile(str(YAML_PATH))
    assert profile_from_dict(profile_to_dict(p)) == p


def test_round_trip_custom_profile():
    p = profile_from_dict({**valid(), "allow_open_jaw": True, "currency": "USD",
                           "preferences": {"avoid_stops": False}})
    assert profile_from_dict(profile_to_dict(p)) == p


def test_to_dict_uses_iso_strings_and_plain_types():
    d = profile_to_dict(profile_from_dict(valid()))
    assert d["earliest_departure"] == "2027-03-19"
    assert d["latest_return"] == "2027-04-15"
    assert d["origins"] == ["FRA"]
    assert isinstance(d["preferences"], dict)
    assert d["preferences"]["avoid_stops"] is True


# --- Alias-Auflösung ------------------------------------------------------

def test_kapverden_aliases_in_airports():
    assert resolve_airports("kapverden") == "SID,BVC,RAI"
    assert resolve_airports("Cape Verde") == "SID,BVC,RAI"


def test_existing_city_aliases_unchanged():
    assert resolve_airports("frankfurt") == "FRA,HHN"
    assert resolve_airports("London") == "LHR,LGW,STN,LTN"


def test_alias_resolved_for_destinations():
    p = profile_from_dict({**valid(), "destinations": "cape verde"})
    assert p.destinations == ["SID", "BVC", "RAI"]


def test_alias_in_origins_list_resolves_and_dedupes():
    p = profile_from_dict({**valid(), "origins": ["frankfurt", "FRA"]})
    assert p.origins == ["FRA", "HHN"]


def test_comma_separated_string_accepted():
    p = profile_from_dict({**valid(), "origins": "FRA, hhn"})
    assert p.origins == ["FRA", "HHN"]


def test_duplicates_removed_keeping_order():
    p = profile_from_dict({**valid(), "origins": "HHN,FRA,hhn,FRA"})
    assert p.origins == ["HHN", "FRA"]


def test_alias_overlap_with_code_deduplicated():
    p = profile_from_dict({**valid(), "destinations": "kapverden,SID"})
    assert p.destinations == ["SID", "BVC", "RAI"]


# --- Validierung ----------------------------------------------------------

def test_valid_profile_accepted():
    assert isinstance(profile_from_dict(valid()), TripProfile)


def test_not_a_mapping():
    with pytest.raises(ValueError, match="Mapping"):
        profile_from_dict(["FRA"])


def test_missing_required_key():
    d = valid()
    del d["origins"]
    with pytest.raises(ValueError, match="Pflichtfeld.*origins"):
        profile_from_dict(d)


def test_unknown_top_level_key():
    with pytest.raises(ValueError, match="Unbekannte Felder.*foo"):
        profile_from_dict({**valid(), "foo": 1})


def test_unknown_preference_key():
    with pytest.raises(ValueError, match="Unbekannte Felder.*preferences"):
        profile_from_dict({**valid(), "preferences": {"avoid_fun": True}})


def test_preference_must_be_bool():
    with pytest.raises(ValueError, match="preferences.avoid_stops"):
        profile_from_dict({**valid(), "preferences": {"avoid_stops": "ja"}})


def test_preferences_must_be_mapping():
    with pytest.raises(ValueError, match="preferences muss ein Mapping"):
        profile_from_dict({**valid(), "preferences": ["avoid_stops"]})


@pytest.mark.parametrize("value", [[], "", "  ", ",", ["", " "]])
def test_airports_zero_rejected(value):
    with pytest.raises(ValueError, match="1 bis 3 Flughäfen"):
        profile_from_dict({**valid(), "origins": value})


def test_airports_more_than_three_rejected():
    with pytest.raises(ValueError, match="1 bis 3 Flughäfen erwartet, gefunden: 4"):
        profile_from_dict({**valid(), "origins": "FRA,HHN,MUC,BER"})


def test_city_alias_with_four_airports_rejected():
    with pytest.raises(ValueError, match="gefunden: 4"):
        profile_from_dict({**valid(), "origins": "london"})


def test_airports_three_accepted():
    p = profile_from_dict({**valid(), "origins": "FRA,MUC,BER"})
    assert p.origins == ["FRA", "MUC", "BER"]


@pytest.mark.parametrize("code", ["FR", "FRAA", "F1A", "fr1", "F-A", "12"])
def test_invalid_iata_rejected(code):
    with pytest.raises(ValueError, match="ungültiger Flughafen-Code"):
        profile_from_dict({**valid(), "origins": code})


def test_airports_list_with_non_string_rejected():
    with pytest.raises(ValueError, match="nur Texte"):
        profile_from_dict({**valid(), "origins": ["FRA", 123]})


def test_airports_wrong_type_rejected():
    with pytest.raises(ValueError, match="Liste oder ein kommagetrennter"):
        profile_from_dict({**valid(), "origins": 42})


def test_earliest_equal_latest_rejected():
    with pytest.raises(ValueError, match="earliest_departure.*vor latest_return"):
        profile_from_dict({**valid(), "earliest_departure": "2027-04-15",
                           "latest_return": "2027-04-15"})


def test_earliest_after_latest_rejected():
    with pytest.raises(ValueError, match="vor latest_return"):
        profile_from_dict({**valid(), "earliest_departure": "2027-05-01"})


def test_invalid_date_string_rejected():
    with pytest.raises(ValueError, match="Ungültiges Datum für earliest_departure"):
        profile_from_dict({**valid(), "earliest_departure": "19.03.2027"})


def test_datetime_rejected_as_date():
    with pytest.raises(ValueError, match="ohne Uhrzeit"):
        profile_from_dict({**valid(), "latest_return": datetime(2027, 4, 15, 10, 0)})


def test_date_objects_accepted():
    p = profile_from_dict({**valid(), "earliest_departure": date(2027, 3, 19)})
    assert p.earliest_departure == date(2027, 3, 19)


def test_nights_below_min_rejected():
    with pytest.raises(ValueError, match="nights .* zwischen min_nights"):
        profile_from_dict({**valid(), "nights": 7})


def test_nights_above_max_rejected():
    with pytest.raises(ValueError, match="nights .* zwischen min_nights"):
        profile_from_dict({**valid(), "nights": 13})


def test_nights_at_bounds_accepted():
    assert profile_from_dict({**valid(), "nights": 8}).nights == 8
    assert profile_from_dict({**valid(), "nights": 12}).nights == 12


@pytest.mark.parametrize("flex", [-1, 4])
def test_flex_days_out_of_range_rejected(flex):
    with pytest.raises(ValueError, match="flex_days muss zwischen 0 und 3"):
        profile_from_dict({**valid(), "flex_days": flex})


@pytest.mark.parametrize("flex", [0, 3])
def test_flex_days_bounds_accepted(flex):
    assert profile_from_dict({**valid(), "flex_days": flex}).flex_days == flex


def test_int_fields_reject_bool_and_float():
    with pytest.raises(ValueError, match="ganze Zahl"):
        profile_from_dict({**valid(), "flex_days": True})
    with pytest.raises(ValueError, match="ganze Zahl"):
        profile_from_dict({**valid(), "nights": 10.0})


def test_allow_open_jaw_must_be_bool():
    with pytest.raises(ValueError, match="allow_open_jaw"):
        profile_from_dict({**valid(), "allow_open_jaw": "yes"})
