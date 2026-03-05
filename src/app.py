# Third Party Imports
import uvicorn
from fastapi import FastAPI

# Local Imports
from src.scripts.router import router

# Make the app
app = FastAPI()


# Start the configurable application
def start_app(host: str = "0.0.0.0", port: int = 8080, root_path: str = ""):
    app.include_router(router)
    uvicorn.run(app=app, host=host, port=port, root_path=root_path)
