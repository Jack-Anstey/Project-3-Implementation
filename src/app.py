# Native Imports
import asyncio
from contextlib import asynccontextmanager

# Third Party Imports
import uvicorn
from fastapi import FastAPI

# Local Imports
from src.scripts.router import ROUTER
from src.scripts.retry_queue import RETRY_QUEUE
from src.scripts.notifications import NOTIFIER
from src.scripts.analytics import TRACKER
from src.scripts.inventory import INVENTORY
from src.scripts.event_bus import EVENT_BUS
from src.scripts.events import ReservationExpired
from src.scripts.event_handlers import register_all
from src.utils.middleware import TimeoutMiddleware
from src.utils.custom_logger import get_logger

# Create the global logger
logger = get_logger(name=__name__, file_name="app.log")


async def _periodic_inventory_cleanup() -> None:
    """Background worker: clean up expired reservations every 2 seconds"""
    while True:
        try:
            cleaned, expired_ids = await INVENTORY.run_cleanup()
            if cleaned > 0:
                logger.info(f"Inventory cleanup released {cleaned} expired reservations")
                # Publish expiration events for saga tracking
                base_ids = set(rid.split(":")[0] for rid in expired_ids)
                for base_id in base_ids:
                    await EVENT_BUS.publish(ReservationExpired(reservation_id=base_id))
        except Exception:
            logger.exception("Inventory cleanup failed")
        await asyncio.sleep(2)


async def _periodic_retry_processing() -> None:
    """Background worker: process pending retry tasks every 1 second"""
    while True:
        try:
            processed = await RETRY_QUEUE.process_pending()
            if processed > 0:
                logger.info(f"Retry worker processed {processed} tasks")
        except Exception:
            logger.exception("Retry processing failed")
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: register handlers and start background workers"""
    logger.info("Application starting up")

    # Register event bus subscribers
    register_all(EVENT_BUS)

    # Register retry handlers
    RETRY_QUEUE.register_handler("send_order_confirmation", NOTIFIER.send_order_confirmation)
    RETRY_QUEUE.register_handler("record_order", TRACKER.record_order)

    # Start background workers
    cleanup_task = asyncio.create_task(_periodic_inventory_cleanup())
    retry_task = asyncio.create_task(_periodic_retry_processing())

    yield

    # Shutdown
    cleanup_task.cancel()
    retry_task.cancel()
    dead = await RETRY_QUEUE.get_dead_letters()
    logger.info(f"Shutdown complete: {len(dead)} dead-letter tasks")


# Start the configurable application
def start_app(
    title: str = "Order Processing System",
    host: str = "0.0.0.0",
    port: int = 8080,
    root_path: str = "",
    workers: int = 1,
    _run: bool = True,
) -> FastAPI:

    # Tag metadata controls Swagger UI ordering and descriptions
    openapi_tags = [
        {"name": "Orders", "description": "Place orders and track saga state"},
        {"name": "Inventory", "description": "Confirm or release reservations, check stock levels"},
        {"name": "Notifications", "description": "Send order and shipping notifications"},
        {"name": "Health", "description": "Application health check"},
        {"name": "Diagnostics", "description": "Internal monitoring: analytics, retry queue, logs, and full saga list"},
    ]

    # Make the app and run the Unicorn server
    app = FastAPI(title=title, root_path=root_path, lifespan=lifespan, openapi_tags=openapi_tags)
    app.add_middleware(TimeoutMiddleware, timeout=30)
    app.include_router(ROUTER)
    if _run:
        uvicorn.run(app=app, host=host, port=port, workers=workers)
    return app
