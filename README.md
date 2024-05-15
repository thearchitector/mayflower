# mayflower

Cross the Atlantic and run Python from Node.js.

The underlying protocol is a forked and trimmed version of [JSPyBridge](https://github.com/extremeheat/JSPyBridge)'s `pythonia` package.

Alterations include:

- Running from a browser context.
- Communicating via a WebSocket instead of a IO stream or pipe.
- Relies on the server being available already rather than spawned on first use.
- Built-in and transparent support for Python's `asyncio`.

This package does not yet supported connections over the internet; the intended use is in suites where the same `localhost` is available to both the browser and Python runtime.

## Usage

This tool is both a Node.js package and Python package.

```sh
$ poetry add mayflower
$ yarn add mayflower
```

To start the websocket server, you can invoke mayflower as a module: `python -m mayflower`.
Using the package in browser will automatically start the websocket client and listen.

For unified applications, like using this tool to invoke Python utilities with a Cypress suite, it's recommended to use `concurrently` to ensure cleanup happens if the websocket fails / disconnects:

```sh
$ yarn add concurrently
$ concurrently "python -m mayflower" "cypress open"
```

See the JSPyBridge [documentation](https://github.com/extremeheat/JSPyBridge/blob/master/docs/javascript.md) for the syntactical sugar.

## License

This package is license under BSD-3-Clause.

JSPyBridge, which this package is based on, is licensed under MIT.
