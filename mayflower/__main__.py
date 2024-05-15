import asyncio

from .interface import Interface

interface = Interface()
asyncio.run(interface.run())
