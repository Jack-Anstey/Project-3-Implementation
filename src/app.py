# Third Party Imports
import uvicorn
from fastapi import FastAPI

# Local Imports
from src.scripts.router import router


# Start the configurable application
def start_app(title: str, host: str = "0.0.0.0", port: int = 8080, root_path: str = ""):

    # Make the app and run the Unicorn server
    app = FastAPI(title=title, root_path=root_path)
    app.include_router(router)
    uvicorn.run(app=app, host=host, port=port)
