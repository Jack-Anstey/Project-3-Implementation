# Native Imports
from pydantic import BaseModel, field_validator


# Input Classes
class Order(BaseModel):
    """A pydantic class that describes an order for our `Order Processing System`

    Args:
        object (str): The name of the `object` ordered
        sku (str): The particular `SKU` of the object ordered
        quantity (int, optional): The `quantity` of the order. Defaults to 1
    """

    object: str
    sku: str
    quantity: int = 1

    def __str__(self) -> str:
        """To str Override method

        Returns:
            str: The str representation of the pydantic `Order` class
        """
        return f"Object: {self.object}, SKU: {self.sku}, Quantity: {self.quantity}"


class ReservationAction(BaseModel):
    """Input model for confirm/release reservation endpoints

    Args:
        reservation_id (str): The base UUID of the reservation to act on
    """

    reservation_id: str


class StockUpdate(BaseModel):
    """Input model for restock endpoint

    Args:
        sku (str): The SKU to restock
        quantity (int): The quantity to add (must be >= 0)
    """

    sku: str
    quantity: int

    @field_validator("quantity")
    @classmethod
    def quantity_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("quantity must be >= 0")
        return v


class OrderNotificationRequest(BaseModel):
    """Input model for order confirmation notification

    Args:
        recipient (str): The notification recipient (email address or phone number)
        channel (str): The notification channel ("email" or "sms")
        reservation_id (str): The reservation UUID this notification relates to
        items (list[dict]): The items included in the order
    """

    recipient: str
    channel: str
    reservation_id: str
    items: list[dict]

    @field_validator("channel")
    @classmethod
    def channel_must_be_valid(cls, v: str) -> str:
        if v not in ("email", "sms"):
            raise ValueError("channel must be 'email' or 'sms'")
        return v


class ShippingNotificationRequest(BaseModel):
    """Input model for shipping confirmation notification

    Args:
        recipient (str): The notification recipient (email address or phone number)
        channel (str): The notification channel ("email" or "sms")
        reservation_id (str): The reservation UUID this notification relates to
        tracking_number (str): The shipment tracking number
    """

    recipient: str
    channel: str
    reservation_id: str
    tracking_number: str

    @field_validator("channel")
    @classmethod
    def channel_must_be_valid(cls, v: str) -> str:
        if v not in ("email", "sms"):
            raise ValueError("channel must be 'email' or 'sms'")
        return v
