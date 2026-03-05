# Project 3: API Example

This is the UW CSE P 590: Cloud Applications Project 3 MVP deployment example

## API Architecture

The following is the the FastAPI directory structure:

```text
└── 📁src
    └── 📁scripts
        ├── responses.py
        ├── router.py
    ├── __main__.py
    └── app.py
├── .gitignore
├── README.md
└── requirements.txt
```

The entrypoint into our application is `app.py`, which gathers our routes from `router.py` and input/output classes from `responses.py` and runs them all together using `uvicorn`, which is bundled with FastAPI. Our `__main__.py` calls `app.py`, which allows us to call main as a module within the `src` directory. This structure enables adding additional routers and routes easily in the future.

## Local Development

To run the application locally, you can simply input `python -m src` in your root directory, or leverage the following `launch.json` for easier debugging:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Run src module",
      "type": "debugpy",
      "request": "launch",
      "module": "src",
      "justMyCode": true,
      "console": "integratedTerminal"
    }
  ]
}
```

You can then view the live application's `Swagger UI` through [http://localhost:8080/docs](http://localhost:8080/docs) by default.
