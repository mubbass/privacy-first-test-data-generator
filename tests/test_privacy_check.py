import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from privacy_check import findLeakedValues

ORIGINAL = "(Müller, Thomas, Breslauer Str. 13, 56269, Dierdorf, Mitglied)"


def test_clean_record_passes():
    concealed = {"last": "Schmidt", "first": "Anna", "street": "Lindenweg 4",
                 "zip": "57610", "city": "Altenkirchen", "status": "Kunde"}
    assert findLeakedValues(ORIGINAL, concealed) == []


def test_exact_value_is_detected():
    assert findLeakedValues(ORIGINAL, {"city": "Dierdorf"}) == ["Dierdorf"]


def test_umlaut_transliteration_is_detected():
    # "Mueller" is the same person as "Müller"
    assert findLeakedValues(ORIGINAL, {"last": "Mueller"}) == ["Müller"]


def test_stripped_diacritics_are_detected():
    # "Muller" without umlaut is detected as well
    assert findLeakedValues(ORIGINAL, {"last": "Muller"}) == ["Müller"]


def test_case_is_ignored():
    assert findLeakedValues(ORIGINAL, {"first": "THOMAS"}) == ["Thomas"]


def test_partial_token_in_new_value_is_detected():
    # street name reused with a different house number
    assert findLeakedValues(ORIGINAL, {"street": "Breslauer Str. 99"}) == ["Breslauer"]


def test_allowed_values_may_stay():
    concealed = {"last": "Schmidt", "status": "Mitglied"}
    assert findLeakedValues(ORIGINAL, concealed) == ["Mitglied"]
    assert findLeakedValues(ORIGINAL, concealed, "Mitglied") == []


def test_allowed_values_do_not_hide_other_leaks():
    concealed = {"city": "Dierdorf", "status": "Mitglied"}
    assert findLeakedValues(ORIGINAL, concealed, "Mitglied") == ["Dierdorf"]


def test_short_parts_and_generic_words_are_not_flagged():
    # house number "13" alone and the generic word "Str" are not personal data
    concealed = {"street": "Gartenstr. 13"}
    assert findLeakedValues(ORIGINAL, concealed) == []


def test_word_boundaries_avoid_false_positives():
    # "Thomasweg" is not the first name "Thomas"
    assert findLeakedValues(ORIGINAL, {"street": "Thomasweg 2"}) == []


def test_field_names_are_ignored():
    # only values are checked, not keys
    assert findLeakedValues(ORIGINAL, {"Dierdorf": "Altenkirchen"}) == []


def test_nested_values_are_checked():
    concealed = {"person": {"names": ["Anna", "Thomas"]}}
    assert findLeakedValues(ORIGINAL, concealed) == ["Thomas"]
