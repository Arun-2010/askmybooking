import logging

from airline_agent.privacy import (
    PnrRedactingFilter,
    extract_credentials,
    mask_name,
    mask_pnr,
    redact_obj,
    redact_text,
)


def test_mask_pnr():
    assert mask_pnr("ABC123") == "AB****"
    assert mask_pnr("abc123") == "AB****"
    assert mask_pnr(None) == "******"
    assert "C123" not in mask_pnr("ABC123")


def test_mask_name():
    assert mask_name("Reddy") == "R****"
    assert "eddy" not in mask_name("Reddy")


def test_extract_credentials_from_question():
    pnr, last = extract_credentials("PNR ABC123, last name Reddy. How much check-in baggage can I take?")
    assert pnr == "ABC123"
    assert last == "Reddy"


def test_extract_credentials_without_keyword_requires_digit():
    pnr, _ = extract_credentials("TRAVEL plans for X7Y8Z9 please")
    assert pnr == "X7Y8Z9"


def test_redact_text():
    out = redact_text("PNR abc123 for Reddy family", "ABC123", ["Reddy"])
    assert "abc123" not in out.lower()
    assert "Reddy" not in out


def test_redact_obj_nested():
    raw = {"pnr": "ABC123", "passengers": [{"firstName": "Asha", "lastName": "Reddy", "type": "ADT"}],
           "contact": {"email": "a@b.c"}}
    red = redact_obj(raw)
    flat = str(red)
    assert "ABC123" not in flat and "Asha" not in flat and "Reddy" not in flat and "a@b.c" not in flat
    assert red["passengers"][0]["type"] == "ADT"


def test_log_filter_masks_pnr(caplog):
    logger = logging.getLogger("privacy-test")
    logger.addFilter(PnrRedactingFilter())
    with caplog.at_level(logging.INFO, logger="privacy-test"):
        logger.info("looking up %s now", "TQ7ZK2")
    assert "TQ7ZK2" not in caplog.text
    assert "TQ****" in caplog.text


def test_redact_obj_live_capture_shapes():
    raw = [{"url": "x", "body": {"bookingInfo": {"recordLocator": "Q1W2E3", "contactEmail": "me@x.com",
            "phoneNumber": "+91 98765 43210", "note": "PNR Q1W2E3 for Kumar, mail me@x.com"},
            "passengers": [{"name": {"first": "Ravi", "last": "Kumar"}, "paxType": "ADT", "dateOfBirth": "1990-01-01"}],
            "journeys": [{"origin": "DEL", "destination": "BLR", "flightNumber": "6E 2134"}]}}]
    red = redact_obj(raw, secrets=["Q1W2E3", "Kumar"])
    flat = str(red)
    for leak in ["Q1W2E3", "Kumar", "Ravi", "me@x.com", "98765", "1990-01-01"]:
        assert leak not in flat, leak
    assert red[0]["body"]["passengers"][0]["paxType"] == "ADT"
    assert red[0]["body"]["journeys"][0] == {"origin": "DEL", "destination": "BLR", "flightNumber": "6E 2134"}
