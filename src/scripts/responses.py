# Native Imports
from pydantic import BaseModel


# Response Classes
class BasicResponse(BaseModel):
    """A simple pydantic class that enables simple returns for the router

    Args:
        response (str): The string contents of the `BasicResponse` object
    """

    response: str
