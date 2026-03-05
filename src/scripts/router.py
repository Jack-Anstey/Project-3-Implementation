# Third Party Imports
from fastapi import APIRouter
from fastapi.requests import Request

# Local Imports
from src.scripts.responses import *

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
