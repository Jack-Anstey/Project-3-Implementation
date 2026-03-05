# Native Imports
from pydantic import BaseModel


# Input Classes
class Order(BaseModel):
    """A pydantic class that describes an order for our `Order Processing System`

    Args:
        response (str): The string contents of the `BasicResponse` object
    """

    object: str
    sku: str
    quantity: int = 1
