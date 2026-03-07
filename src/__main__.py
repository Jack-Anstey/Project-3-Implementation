# Third Party Imports
from src.app import start_app


def main():
    # The app can be configured through environment variables for flexible deployments here
    start_app(title="Order Processing System", root_path="/order-processing")


if __name__ == "__main__":
    main()
