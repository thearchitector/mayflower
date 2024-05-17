import asyncio
import signal
from typing import TYPE_CHECKING

import orjson
from websockets.server import serve

from .bridge import Bridge

if TYPE_CHECKING:  # pragma: no cover
    from typing import Any


class IPC:
    def __init__(self, websocket, should_stop):
        self.websocket = websocket
        self.should_stop = should_stop

    def _default(self, obj):
        if attr := getattr(obj.__class__, "__json__", None):
            return attr(obj)

        raise TypeError()

    def json_loads(self, data: bytes) -> "Any":
        return orjson.loads(data)

    def json_dumps(self, what: "Any") -> str:
        # browsers only support 53-bit integers, while Python supports 64-bit
        return orjson.dumps(
            what, option=orjson.OPT_STRICT_INTEGER, default=self._default
        ).decode()

    async def queue(self, what):
        try:
            data = self.json_dumps(what)
            await self.websocket.send(data)
        except Exception:
            pass


class Interface:
    async def on_message(self, websocket):
        bridge = Bridge(IPC(websocket, self.should_stop))

        async for data in websocket:
            if data[0] != "{":
                continue

            j = bridge.ipc.json_loads(data)
            await bridge.onMessage(j["r"], j["action"], j["ffid"], j["key"], j["val"])

    async def run(self):
        self.loop = asyncio.get_running_loop()
        self.should_stop = self.loop.create_future()
        self.loop.add_signal_handler(signal.SIGTERM, self.should_stop.set_result, None)
        self.loop.add_signal_handler(signal.SIGINT, self.should_stop.set_result, None)

        async with serve(self.on_message, "localhost", 8768, compression=None):
            print("Mayflower listening on ws://localhost:8768")
            await self.should_stop
