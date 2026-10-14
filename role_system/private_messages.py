"""私聊连续来信等待停顿后组成一轮；等待期间不占用角色回合锁。"""
import asyncio
import time
from dataclasses import dataclass, field
from .ports import Event, Plain, logger


@dataclass
class PendingMessages:
    events: list
    first: float
    last: float
    result: asyncio.Future
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None


def merged_event(events):
    last = events[-1]
    text = '\n'.join(event.message_str.strip() for event in events if event.message_str.strip())
    event = Event(last.host, last.unified_msg_origin, last.user_id, last.name, text,
                  addressed=last.is_at_or_wake_command,
                  message_id='batch:' + ','.join(str(e.message_obj.message_id) for e in events),
                  parts=[Plain(text)] + [part for e in events for part in e.parts if not isinstance(part, Plain)])
    event.set_extra('_private_message_batch', tuple(events))
    return event


class PrivateMessages:
    def __init__(self, lock, consume, store, get_config):
        self.lock, self.consume, self.store, self.get_config = lock, consume, store, get_config
        self.pending = {}
        self.tasks = set()
        self.closing = False

    def deadline(self, batch):
        if not self.get_config('private_message_merge_enabled', True):
            return batch.last
        quiet = float(self.get_config('private_message_merge_wait', 3))
        maximum = float(self.get_config('private_message_merge_max_wait', 15))
        return min(batch.last + quiet, batch.first + maximum)

    async def receive(self, event):
        if self.closing:
            return 'shutdown'
        key = (event.unified_msg_origin, event.user_id)
        now = asyncio.get_running_loop().time()
        event.set_extra('_private_received_at', time.time())
        batch = self.pending.get(key)
        if batch is None:
            batch = PendingMessages([], now, now, asyncio.get_running_loop().create_future())
            self.pending[key] = batch
            task = asyncio.create_task(self.run(key, batch))
            batch.task = task
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        batch.events.append(event)
        batch.last = now
        batch.changed.set()
        return await asyncio.shield(batch.result)

    async def run(self, key, batch):
        try:
            while True:
                batch.changed.clear()
                delay = self.deadline(batch) - asyncio.get_running_loop().time()
                if delay > 0:
                    try:
                        await asyncio.wait_for(batch.changed.wait(), timeout=delay)
                        continue
                    except asyncio.TimeoutError:
                        pass
                async with self.lock:
                    # 排队等待其他回合时仍可收信；真正开始前再次检查停顿。
                    if self.deadline(batch) > asyncio.get_running_loop().time():
                        continue
                    del self.pending[key]
                    logger.info('[私聊合并] %s | %s 条来信组成一轮', key[0], len(batch.events))
                    result = await self.consume(merged_event(batch.events))
                    batch.result.set_result(result)
                    return
        except asyncio.CancelledError:
            if not batch.result.done():
                batch.result.set_result('shutdown')
            raise
        except Exception as error:
            if self.pending.get(key) is batch:
                del self.pending[key]
            batch.result.set_exception(error)

    async def close(self):
        # 停机前保存尚未成轮的原始来信，不在关闭阶段调用模型。
        self.closing = True
        waiting = list(self.pending.values())
        for batch in waiting:
            batch.task.cancel()
        await asyncio.gather(*(batch.task for batch in waiting), return_exceptions=True)
        async with self.lock:
            for batch in waiting:
                self.store(merged_event(batch.events))
            self.pending.clear()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
