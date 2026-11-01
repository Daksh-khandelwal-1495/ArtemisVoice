from datetime import date

import pytest

from app.schemas.flow import CaptureField, FieldValidation
from app.voice.validators import (
    is_empty,
    mask_value,
    match_enum,
    parse_amount,
    parse_bool,
    parse_date,
    parse_email,
    parse_number,
    parse_pan,
    parse_phone,
    parse_pincode,
    parse_vehicle_reg,
    validate_field,
    words_to_number,
)

TODAY = date(2026, 11, 1)  # a Sunday


def F(type_, **kw):
    v = kw.pop("validation", {})
    return CaptureField(name="f", label="Field", type=type_, validation=FieldValidation(**v), **kw)


@pytest.mark.parametrize("raw,exp", [
    ("9876543210", "9876543210"), ("+91 98765 43210", "9876543210"), ("+91-9876543210", "9876543210"),
    ("09876543210", "9876543210"), ("919876543210", "9876543210"), (9876543210, "9876543210"),
    ("nine eight seven six five four three two one zero", "9876543210"),
    ("double nine eight seven six five four three two one", "9987654321"),
])
def test_phone_ok(raw, exp):
    assert parse_phone(raw) == exp


@pytest.mark.parametrize("raw", ["5876543210", "98765", "98765432101", "abc", "+1 415 555 0100"])
def test_phone_bad(raw):
    with pytest.raises(ValueError):
        parse_phone(raw)


def test_pincode():
    assert parse_pincode("400 001") == "400001"
    assert parse_pincode(560001) == "560001"
    assert parse_pincode("five six zero zero zero one") == "560001"
    for bad in ["012345", "12345", "1234567"]:
        with pytest.raises(ValueError):
            parse_pincode(bad)


def test_pan():
    assert parse_pan("abcde1234f") == "ABCDE1234F"
    assert parse_pan("ABCDE 1234 F") == "ABCDE1234F"
    assert parse_pan("A B C D E 1 2 3 4 F") == "ABCDE1234F"
    for bad in ["ABCD1234F", "ABCDE12345", "12345ABCDE"]:
        with pytest.raises(ValueError):
            parse_pan(bad)


def test_vehicle_reg():
    assert parse_vehicle_reg("MH12AB1234") == "MH12AB1234"
    assert parse_vehicle_reg("mh 12 ab 1234") == "MH12AB1234"
    assert parse_vehicle_reg("DL-3C-AB-1234") == "DL3CAB1234"
    assert parse_vehicle_reg("KA01A1234") == "KA01A1234"
    assert parse_vehicle_reg("22BH1234AB") == "22BH1234AB"
    with pytest.raises(ValueError):
        parse_vehicle_reg("1234")


@pytest.mark.parametrize("raw,exp", [
    ("5 lakh", 500000), ("2.5 crore", 25000000), ("50k", 50000), ("50K", 50000),
    ("fifty thousand", 50000), ("₹5,00,000", 500000), ("Rs. 75000", 75000), (300000, 300000),
    ("one lakh fifty thousand", 150000), ("2 cr", 20000000), ("ten lakh rupees", 1000000),
    ("1.2 million", 1200000), ("two point five lakh", 250000), ("5 lakhs", 500000),
    ("twenty five thousand", 25000), ("one hundred twenty", 120),
])
def test_amount(raw, exp):
    assert parse_amount(raw) == exp


def test_amount_bad():
    with pytest.raises(ValueError):
        parse_amount("a lot")


def test_words_to_number():
    assert words_to_number("twenty one") == 21
    assert words_to_number("nothing here") is None


def test_number_integer():
    assert parse_number("45") == 45
    assert parse_number("3.5") == 3.5
    assert parse_number("about 45 thousand km", integer=True) == 45000
    with pytest.raises(ValueError):
        parse_number("3.5", integer=True)


@pytest.mark.parametrize("raw,exp", [
    ("yes", True), ("Haan", True), ("haan ji", True), ("yeah sure", True), (True, True),
    ("no", False), ("nahi", False), ("Nope", False), (False, False), ("no thanks", False),
])
def test_bool(raw, exp):
    assert parse_bool(raw) is exp


def test_bool_bad():
    with pytest.raises(ValueError):
        parse_bool("maybe later perhaps")


def test_enum_fuzzy():
    opts = ["Nexon", "Nexon EV", "Harrier", "Safari", "Punch"]
    assert match_enum("nexon", opts) == "Nexon"
    assert match_enum("NEXON ev", opts) == "Nexon EV"
    assert match_enum("the nexon ev please", opts) == "Nexon EV"
    assert match_enum("Hariar", opts) == "Harrier"
    assert match_enum("safari.", opts) == "Safari"
    with pytest.raises(ValueError):
        match_enum("Innova", opts)
    assert match_enum("personal loan", ["personal", "home"]) == "personal"
    assert match_enum("home_loan", ["home_loan", "personal_loan"]) == "home_loan"


def test_email():
    assert parse_email("John.Doe@Example.com") == "john.doe@example.com"
    assert parse_email("john dot doe at gmail dot com") == "john.doe@gmail.com"
    with pytest.raises(ValueError):
        parse_email("john at nowhere")


@pytest.mark.parametrize("raw,exp", [
    ("tomorrow", date(2026, 11, 2)), ("today", TODAY), ("day after tomorrow", date(2026, 11, 3)),
    ("2026-12-25", date(2026, 12, 25)), ("25/12/2026", date(2026, 12, 25)), ("5-1-27", date(2027, 1, 5)),
    ("15 August 2027", date(2027, 8, 15)), ("Aug 15th 2027", date(2027, 8, 15)),
    ("next monday", date(2026, 11, 2)), ("friday", date(2026, 11, 6)), ("in 3 days", date(2026, 11, 4)),
    ("in two weeks", date(2026, 11, 15)), ("the 20th", date(2026, 11, 20)),
])
def test_dates(raw, exp):
    assert parse_date(raw, today=TODAY) == exp


def test_date_year_rollover_future():
    assert parse_date("15 March", today=TODAY, prefer_future=True) == date(2027, 3, 15)
    assert parse_date("15 March", today=TODAY) == date(2026, 3, 15)


def test_validate_date_rules():
    fut = F("date", validation={"future_only": True})
    assert validate_field(fut, "tomorrow", today=TODAY).value == "2026-11-02"
    r = validate_field(fut, "yesterday", today=TODAY)
    assert not r.ok and "future" in r.error
    past = F("date", validation={"past_only": True})
    assert validate_field(past, "12/05/1990", today=TODAY).value == "1990-05-12"
    assert not validate_field(past, "tomorrow", today=TODAY).ok
    assert not validate_field(F("date"), "someday", today=TODAY).ok


def test_validate_min_max_regex():
    f = F("amount", validation={"min": 50000, "max": 4000000})
    assert validate_field(f, "5 lakh").value == 500000
    assert "at least" in validate_field(f, "10k").error
    assert "at most" in validate_field(f, "5 crore").error
    s = F("string", validation={"min": 2, "max": 5})
    assert validate_field(s, "abc").ok
    assert not validate_field(s, "a").ok
    assert not validate_field(s, "abcdefg").ok
    r = F("string", validation={"regex": r"[A-Z]{3}\d{2}"})
    assert validate_field(r, "ABC12").ok
    assert not validate_field(r, "abc").ok
    bad_regex = F("string", validation={"regex": "("})
    assert validate_field(bad_regex, "x").ok  # invalid regex never blocks a call


def test_validate_types_and_empty():
    assert validate_field(F("integer"), "42").value == 42
    assert validate_field(F("boolean"), "haan").value is True
    assert validate_field(F("enum", validation={"options": ["Red", "Blue"]}), "blue").value == "Blue"
    assert validate_field(F("aadhaar_last4"), "1 2 3 4").value == "1234"
    assert not validate_field(F("aadhaar_last4"), "123").ok
    assert validate_field(F("pan"), "abcde1234f").value == "ABCDE1234F"
    for raw in (None, "", "  ", "unknown", "N/A"):
        assert is_empty(raw)
        assert not validate_field(F("string"), raw).ok


def test_mask():
    assert mask_value("ABCDE1234F") == "******234F"
    assert mask_value("123") == "***"
    assert mask_value(None) == ""
