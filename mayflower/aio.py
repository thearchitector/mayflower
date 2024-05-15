import asyncio


# please do not look at this
def run_from_sync(coro):
    loop = asyncio.get_running_loop()
    current_task = asyncio.current_task(loop=loop)

    task = asyncio.create_task(coro)
    asyncio.tasks._leave_task(loop, current_task)

    while not task.done():
        loop._run_once()
        if loop._stopping:
            break

    res = task.result()
    asyncio.tasks._enter_task(loop, current_task)
    return res
