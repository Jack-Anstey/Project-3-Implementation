# Third Party imports
from locust import HttpUser, task, between

# Local Imports
from src.scripts.inputs import Order


# Generated with some help from Claude
class APIUser(HttpUser):
    wait_time = between(0, 0.5)  # seconds between requests

    @task(1)
    def get_api_status(self):
        self.client.get("/health")

    @task(10)
    def create_item(self):

        # Define some random set of Orders
        items = [
            Order(object="Synthetic Load", sku="ABC123", quantity=10),
            Order(object="Synthetic Load 1", sku="12345", quantity=1),
            Order(object="Synthetic Load 2", sku="XYZ", quantity=100),
        ]

        # Post it
        self.client.post(
            url="/order-intake",
            json=[item.model_dump() for item in items],
        )
