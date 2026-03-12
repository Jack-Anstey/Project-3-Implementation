# Project 3: Order Service Example

This is the UW CSE P 590: Cloud Applications Project 3 MVP deployment example for Team 5.
In particular, it is a small scale representation of the "Order Service" Lambda service as seen in our architecture diagram.

## Project Members

- Jack Anstey
- Aaron Huber
- Yogesh Balaje Mahendron
- Tyler Reitz
- Mitali Shenoy
- Javier Contreras Tenorio

## API Architecture

The following is the the FastAPI directory structure:

```text
└── 📁src
    └── 📁scripts
        ├── inputs.py
        ├── responses.py
        ├── router.py
    ├── __main__.py
    └── app.py
└── 📁tests
    ├── synthetic_load.py
├── .gitignore
├── README.md
└── requirements.txt
```

The entrypoint into our application is `app.py`, which gathers our routes from `router.py` and input/output classes from `inputs.py` and `responses.py`. Then, they are all ran together using `uvicorn`, which is bundled with FastAPI. Our `__main__.py` calls `app.py`, which allows us to call main as a module within the `src` directory. This structure enables adding additional routers and routes easily in the future.

The `tests` directory enables us to place a synthetic load onto our application to see how many users it can handle concurrently. Leveraging it can be seen in the [synthetic testing section](#synthetic-testing) of the readme.

## Synthetic Testing

When you have the application running, adding a synthetic test on the endpoints is very easy. Using a `locust` script, we can artificially place whatever load we want on our endpoints. Below is a bash script that places a load of roughly 180-200 requests on our `order-intake` endpoint:

```bash
locust -f tests/synthetic_load.py --host=http://localhost:8080/order-processing --users 50 --spawn-rate 5 --run-time 60s --headless
```

You can adjust the number of users, spawn rate, and run time to test other scenarios to see if our asynchronous endpoints can handle them.

## Local Development

To run the application locally, you can simply input `python -m src` in your root directory, or leverage the following `launch.json` for easier debugging:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Launch API",
      "type": "debugpy",
      "request": "launch",
      "module": "src",
      "justMyCode": true,
      "console": "integratedTerminal"
    }
  ]
}
```

You can then view the live application's `Swagger UI` through [http://localhost:8080/order-processing/docs](http://localhost:8080/order-processing/docs) by default.

## Infrastructure as Code (IAC)

The IAC of this project is relatively simple:

The `docker` directory hosts all of our files needed for containerization, which enables us to specific in our dependency handling before pushing to AWS Lambda (serverless). Only the [Lambda Web Adaptor configurations](https://github.com/awslabs/aws-lambda-web-adapter?tab=readme-ov-file#configurations) remain for a completely deployable IAC app.

Since we are using a specific `ENTRYPOINT` of `python -m src` to start the application, the API is more a traditional web app rather than a start/stop Lambda handler container. For deployment, we'd use the [Lambda Web Adapter](https://github.com/awslabs/aws-lambda-web-adapter) for this specific implementation. We then get all the benefits of Lambda while keeping development simple: pulling the latest version of a given Docker container as needed.
