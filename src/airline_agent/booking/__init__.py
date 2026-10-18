from airline_agent.booking.base import (
    BookingError,
    BookingNotFound,
    BookingSource,
    CaptchaRequired,
    SourceUnavailable,
)

__all__ = ["BookingError", "BookingNotFound", "BookingSource", "CaptchaRequired", "SourceUnavailable", "make_source"]


def make_source(name: str, **kwargs) -> BookingSource:
    """Factory so callers (agent, UI, eval) never import a concrete source directly."""
    if name == "mock_testair":
        from airline_agent.booking.mock_testair import MockTestAirSource

        return MockTestAirSource(**kwargs)
    if name == "eticket":
        from airline_agent.booking.eticket import ETicketSource

        return ETicketSource(**kwargs)
    if name == "live_indigo":
        from airline_agent.booking.live_indigo import LiveIndiGoSource

        return LiveIndiGoSource(**kwargs)
    raise ValueError(f"unknown booking source: {name}")
