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

    def __str__(self) -> str:
        """To str Override method

        Returns:
            str: The str representation of the pydantic `Order` class
        """
        return f"Object: {self.object}, SKU: {self.sku}, Quantity: {self.quantity}"
