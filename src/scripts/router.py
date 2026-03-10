# Third Party Imports
from fastapi import APIRouter
from fastapi.requests import Request

# Local Imports
from src.scripts.responses import *
from src.scripts.inputs import *
from src.utils.custom_logger import get_logger

# Create the global logger
logger = get_logger(name=__name__, file_name="orders.log")


# Define the router that we will use
ROUTER = APIRouter(tags=["Project 3 Example"])


# Define get and post requests
@ROUTER.get("/health")
async def root_status(request: Request) -> BasicResponse:
    """Get the status of the FastAPI application

    Args:
        request (Request): The HTTP request

    Returns:
        BasicResponse: A basic response of Hello World!
    """

    logger.info("Logging works for the root endpoint")
    return BasicResponse(response="Healthy!")


@ROUTER.post("/order-intake")
async def take_order(request: Request, orders: list[Order]) -> BasicResponse:
    """Take in an order to submit it to our database

    Args:
        request (Request): The HTTP request
        orders (list[Order]): A list of `Orders` that comprise a user's total order

    Returns:
        BasicResponse: A basic response if the order was successful or not
    """

    # TODO order is sent to our Redis cached database

    # Log the user's order
    logger.info(f"The user's order:")
    [logger.info(f"\t{order}") for order in orders]

    # Return that it was successful
    return BasicResponse(response="Order was successful")
