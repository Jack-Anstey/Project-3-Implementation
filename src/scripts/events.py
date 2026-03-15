# Native Imports
from dataclasses import dataclass


@dataclass(frozen=True)
class OrderValidated:
    """Emitted after order validation and soft reservation"""

    reservation_id: str
    orders: list
    order_response: object
    status_code: int
    idempotency_key: str | None


@dataclass(frozen=True)
class ReservationConfirmed:
    """Emitted after a reservation is confirmed (payment succeeded)"""

    reservation_id: str


@dataclass(frozen=True)
class ReservationReleased:
    """Emitted after a reservation is released (payment failed / cancelled)"""

    reservation_id: str


@dataclass(frozen=True)
class ReservationExpired:
    """Emitted when a reservation expires via cleanup worker"""

    reservation_id: str
