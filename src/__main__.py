# Third Party Imports
from src.app import start_app


def main():
    # TODO configure the app through environment variables for flexible deployments here
    start_app(title="Order Processing System", root_path="/order-processing")


if __name__ == "__main__":
    main()
