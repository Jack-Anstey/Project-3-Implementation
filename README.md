# Project 3: API Example

This is the UW CSE P 590: Cloud Applications Project 3 MVP deployment example

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

You can then view the live applications `Swagger UI` through [http://localhost:8080/docs](http://localhost:8080/docs) by default.
