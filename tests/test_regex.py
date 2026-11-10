from deid.methods.regex import RegexMethod


def found(text):
    return {(text[s.start : s.end], s.category) for s in RegexMethod().predict(text).spans}


def test_phone_formats():
    text = "Call (555) 123-4567 or 555-123-4567 or 555.123.4567 or +1 555 123 4567."
    assert {t for t, c in found(text) if c == "PHONE"} == {
        "(555) 123-4567",
        "555-123-4567",
        "555.123.4567",
        "+1 555 123 4567",
    }


def test_email_ssn_mrn_zip():
    text = (
        "jo.doe7@gmail.com SSN: 123 45 6789 and 987-65-4321. MRN: 00123456, Durham, NC 27701-1234"
    )
    got = found(text)
    assert ("jo.doe7@gmail.com", "EMAIL") in got
    assert ("123 45 6789", "SSN") in got
    assert ("987-65-4321", "SSN") in got
    assert ("00123456", "MRN") in got
    assert ("27701-1234", "ZIP") in got


def test_date_formats():
    text = "Seen 03/14/2024, 3/14/24, 2024-03-14, March 14, 2024, Mar 14, 14-Mar-2024, 3/14, March 2024."
    dates = {t for t, c in found(text) if c == "DATE"}
    assert dates == {
        "03/14/2024",
        "3/14/24",
        "2024-03-14",
        "March 14, 2024",
        "Mar 14",
        "14-Mar-2024",
        "3/14",
        "March 2024",
    }


def test_does_not_attempt_names_and_avoids_obvious_numbers():
    text = (
        "John Smith, BP 135/92, carbidopa-levodopa 25/100 mg, diagnosed in 2015, MRN-less 12345678"
    )
    assert found(text) == set()


def test_known_weakness_strength_score_looks_like_a_date():
    # Documented false positive: motor strength "4/5" matches month/day.
    assert ("4/5", "DATE") in found("strength 4/5 in the left upper extremity")
