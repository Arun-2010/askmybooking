import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from airline_agent.booking.base import BookingNotFound, map_testair_json
from airline_agent.booking.eticket import ETicketSource
from mock_site.app import materialise

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from make_test_etickets import render  # noqa: E402

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("idx", [0, 11, 16, 20, 24, 30, 33])
def test_eticket_roundtrip(tmp_path, seed, idx):
    b = seed[idx]
    data = materialise(b, NOW)
    pdf = tmp_path / "t.pdf"
    render(data, pdf)
    got = ETicketSource(pdf).get_booking(b["pnr"], b["last_name"])
    want = map_testair_json(data, source="eticket")
    assert got.model_dump() == want.model_dump()


def test_eticket_rejects_wrong_name(tmp_path, seed):
    pdf = tmp_path / "t.pdf"
    render(materialise(seed[0], NOW), pdf)
    with pytest.raises(BookingNotFound):
        ETicketSource(pdf).get_booking(seed[0]["pnr"], "Someoneelse")


def test_eticket_rejects_wrong_pnr(tmp_path, seed):
    pdf = tmp_path / "t.pdf"
    render(materialise(seed[0], NOW), pdf)
    with pytest.raises(BookingNotFound):
        ETicketSource(pdf).get_booking("TXXXX1", seed[0]["last_name"])
