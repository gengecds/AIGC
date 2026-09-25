"""进程级「优雅关闭」信号 —— 供各 SSE 长连接生成器收尾使用。

背景：uvicorn 关闭时只等连接自己释放（`Server._wait_tasks_to_complete`），
**不会 cancel ASGI 任务**，而 SSE 生成器是无限循环，于是停机/热重载必然拖到
`timeout_graceful_shutdown` 到点被硬 cancel（reloader 的 process.join() 随之阻塞）。
各生成器改用 `wait_or_shutdown()` 等待，收到本信号就主动推一条收尾事件并 return，
连接在下一个事件循环即可释放。

触发点是**信号处理器**，不是 lifespan 退出：uvicorn 的 `Server.shutdown()` 会先
`_wait_tasks_to_complete()` 等连接释放，之后才跑 lifespan 退出——在 lifespan 里发信号
已经太晚（连接早被 `timeout_graceful_shutdown` 硬 cancel）。由 api.main 的 lifespan
启动时 `install_signal_hook()`（链到 uvicorn 自己的处理器）、`reset_shutdown()`，
退出时再 `request_shutdown()` 兜底。
"""

import asyncio
import signal
import threading
from collections.abc import Callable

# 服务端正在关闭的标记（启动时 clear，兼容同进程内多次 lifespan）
shutdown_event = asyncio.Event()

# wait_or_shutdown 的返回值：等待被关闭信号打断（而非正常完成）。超时也走这个分支，
# 调用方用 shutdown_event.is_set() 区分「关闭」与「超时」。
INTERRUPTED = object()

# 与 uvicorn.server.HANDLED_SIGNALS 保持一致：Ctrl+C / kill
_HANDLED_SIGNALS = (signal.SIGINT, signal.SIGTERM)


def request_shutdown() -> None:
    """标记服务端进入关闭流程。"""
    shutdown_event.set()


def reset_shutdown() -> None:
    """清除关闭标记（lifespan 启动时调用，避免沿用上一轮状态）。"""
    shutdown_event.clear()


def install_signal_hook() -> Callable[[], None]:
    """把关闭信号接到本模块，返回「还原原处理器」的函数。

    信号一到先 `request_shutdown()`（唤醒各 SSE 生成器收尾），再链式调用原处理器
    ——通常是 uvicorn 的 `Server.handle_exit`，由它置 should_exit 让主循环退出。
    信号处理器只在主线程执行，`asyncio.Event.set()` 在同线程内是安全的。
    """
    if threading.current_thread() is not threading.main_thread():
        return lambda: None

    restore_list: list[tuple[int, object]] = []
    for sig in _HANDLED_SIGNALS:
        prev = signal.getsignal(sig)

        def handler(signum, frame, _prev=prev):
            request_shutdown()
            if callable(_prev):
                _prev(signum, frame)

        signal.signal(sig, handler)
        restore_list.append((sig, prev))

    def restore() -> None:
        for sig, prev in restore_list:
            signal.signal(sig, prev)

    return restore


async def wait_or_shutdown(awaitable, timeout: float | None = None):
    """等 awaitable 完成，但被关闭信号打断时立刻返回 INTERRUPTED。

    超时同样返回 INTERRUPTED（同步等不到结果），调用方据 shutdown_event 判断原因。
    `q.get()` / `Event.wait()` / `StreamReader.readline()` 均可安全取消。
    """
    task = asyncio.ensure_future(awaitable)
    waiter = asyncio.ensure_future(shutdown_event.wait())
    done: set = set()
    try:
        done, _ = await asyncio.wait(
            {task, waiter}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        waiter.cancel()
        if not done:
            # 外层被取消（如到点硬 cancel）：把这次等待一起收掉，别留悬挂任务
            task.cancel()
    if task in done:
        return task.result()
    task.cancel()
    return INTERRUPTED
