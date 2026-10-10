"""Do not release an owning lock while cancelled synchronous work still runs."""
import asyncio
import logging


async def settled_thread(function, *args, **kwargs):
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:  # noqa: BLE001 — retrieve/log failure below before releasing the owning lock
                break
        try:
            task.result()
        except Exception:
            logging.getLogger(__name__).exception('取消时同步任务也发生错误')
        raise
