# Third Party Imports
from fastapi import APIRouter
from fastapi.requests import Request

# Local Imports
from src.scripts.responses import *
from src.scripts.inputs import *

# Define the router that we will use
router = APIRouter(tags=["Project 3 Example"])


# Define get and post requests
@router.get("/")
async def root_status(request: Request) -> BasicResponse:
    """Get the status of the FastAPI application

    Args:
        request (Request): The HTTP request

    Returns:
        BasicResponse: A basic response of Hello World!
    """

    return BasicResponse(response="Hello World!")


@router.post("/order-intake")
async def take_order(request: Request, orders: list[Order]) -> BasicResponse:
    """Get the

    Args:
        request (Request): The HTTP request
        orders (list[Order]): A list of `Orders` that comprise a user's total order

    Returns:
        BasicResponse: A basic response if the order was successful or not
    """

    # TODO order is sent to our Redis cached database
    return BasicResponse(response="Order was successful")
