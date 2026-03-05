# Native Imports
from pydantic import BaseModel


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
