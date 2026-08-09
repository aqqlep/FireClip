"""任务队列与并发控制工具

多任务排队串行执行，严格限制同时运行的 AI 推理任务数量。
支持暂停、终止、进度回调。
"""
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from logger import get_logger
from hardware_monitor import HardwareGuard

log = get_logger("task")


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    ERROR = "error"
    CANCELED = "canceled"


@dataclass
class Task:
    """任务对象"""
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    module: str = ""               # fight / highlight / shortdrama
    name: str = ""
    input_files: list = field(default_factory=list)
    config: dict = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0          # 0.0 ~ 1.0
    current_file: str = ""
    current_time: float = 0.0
    total_duration: float = 0.0
    result: dict = field(default_factory=dict)
    error: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    # 执行函数（由业务模块注入）
    executor: Optional[Callable] = None
    # 进度回调
    on_progress: Optional[Callable] = None
    _cancel_event: threading.Event = field(default_factory=threading.Event)
    _pause_event: threading.Event = field(default_factory=lambda: threading.Event())

    def __post_init__(self):
        self._pause_event.set()  # 默认非暂停状态


class TaskScheduler:
    """任务队列并发控制器（单例）

    严格限制同一时间仅运行 1 路 AI 推理任务。
    """

    _instance: Optional["TaskScheduler"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, max_concurrent: int = 1):
        if self._initialized:
            return
        self._max_concurrent = max_concurrent
        self._queue: deque = deque()
        self._running: dict = {}  # {task_id: Task}
        self._history: list = []
        self._queue_lock = threading.Lock()
        self._worker_thread: Optional[threading.Thread] = None
        self._running_flag = False
        self._guard = HardwareGuard()
        self._listeners: list[Callable] = []
        self._initialized = True
        log.info(f"任务调度器初始化，最大并发: {max_concurrent}")

    def start(self) -> None:
        """启动调度线程"""
        if self._running_flag:
            return
        self._running_flag = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="TaskScheduler")
        self._worker_thread.start()
        log.info("任务调度线程已启动")

    def stop(self) -> None:
        """停止调度"""
        self._running_flag = False
        # 取消所有等待中任务
        with self._queue_lock:
            while self._queue:
                t = self._queue.popleft()
                t.status = TaskStatus.CANCELED
                self._history.append(t)

    def submit(self, task: Task) -> str:
        """提交任务到队列"""
        with self._queue_lock:
            self._queue.append(task)
        log.info(f"任务已入队: {task.task_id} ({task.name})")
        self._notify_listeners()
        return task.task_id

    def cancel(self, task_id: str) -> bool:
        """取消任务"""
        with self._queue_lock:
            for t in self._queue:
                if t.task_id == task_id:
                    t.status = TaskStatus.CANCELED
                    self._queue.remove(t)
                    self._history.append(t)
                    log.info(f"任务已取消: {task_id}")
                    return True
        if task_id in self._running:
            self._running[task_id]._cancel_event.set()
            return True
        return False

    def pause(self, task_id: str) -> bool:
        """暂停任务"""
        if task_id in self._running:
            self._running[task_id]._pause_event.clear()
            self._running[task_id].status = TaskStatus.PAUSED
            log.info(f"任务已暂停: {task_id}")
            return True
        return False

    def resume(self, task_id: str) -> bool:
        """恢复任务"""
        if task_id in self._running:
            self._running[task_id]._pause_event.set()
            self._running[task_id].status = TaskStatus.RUNNING
            log.info(f"任务已恢复: {task_id}")
            return True
        return False

    def get_queue_status(self) -> dict:
        """获取队列状态"""
        with self._queue_lock:
            return {
                "pending": len(self._queue),
                "running": len(self._running),
                "history": len(self._history),
                "queue": [t.task_id for t in self._queue],
                "running_ids": list(self._running.keys()),
            }

    def get_task(self, task_id: str) -> Optional[Task]:
        """获取任务对象"""
        with self._queue_lock:
            for t in self._queue:
                if t.task_id == task_id:
                    return t
        if task_id in self._running:
            return self._running[task_id]
        for t in self._history:
            if t.task_id == task_id:
                return t
        return None

    def add_listener(self, callback: Callable) -> None:
        """注册任务状态变更监听器"""
        self._listeners.append(callback)

    def _notify_listeners(self) -> None:
        for cb in self._listeners:
            try:
                cb(self.get_queue_status())
            except Exception:
                pass

    def _worker_loop(self) -> None:
        """调度主循环"""
        while self._running_flag:
            if len(self._running) >= self._max_concurrent:
                time.sleep(0.2)
                continue

            # 硬件保护：温度过高不启动新任务
            if not self._guard.can_run_new_task():
                time.sleep(1.0)
                continue

            with self._queue_lock:
                if not self._queue:
                    time.sleep(0.2)
                    continue
                task = self._queue.popleft()

            self._execute_task(task)

    def _execute_task(self, task: Task) -> None:
        """执行单个任务"""
        if task._cancel_event.is_set():
            task.status = TaskStatus.CANCELED
            self._history.append(task)
            return

        task.status = TaskStatus.RUNNING
        self._running[task.task_id] = task
        log.info(f"开始执行任务: {task.task_id} ({task.name})")
        self._notify_listeners()

        try:
            if task.executor is None:
                raise ValueError("任务未设置执行函数")

            # 注入控制对象供执行器检查
            ctx = TaskContext(
                task=task,
                guard=self._guard,
                check_cancel=lambda: task._cancel_event.is_set(),
                check_pause=lambda: task._pause_event.wait(),
                update_progress=self._make_progress_updater(task),
            )
            result = task.executor(ctx)
            if task._cancel_event.is_set():
                task.status = TaskStatus.CANCELED
            else:
                task.status = TaskStatus.DONE
                task.progress = 1.0
                task.result = result or {}
                task.finished_at = time.time()
                log.info(f"任务完成: {task.task_id}")
        except Exception as e:
            task.status = TaskStatus.ERROR
            task.error = str(e)
            task.finished_at = time.time()
            log.error(f"任务失败: {task.task_id} - {e}",
                      extra={"category": "error"})

        self._running.pop(task.task_id, None)
        self._history.append(task)
        self._notify_listeners()

    def _make_progress_updater(self, task: Task) -> Callable:
        """创建进度更新闭包"""
        def updater(progress: float, current_file: str = "",
                    current_time: float = 0.0):
            task.progress = max(0.0, min(1.0, progress))
            if current_file:
                task.current_file = current_file
            if current_time:
                task.current_time = current_time
            if task.on_progress:
                try:
                    task.on_progress(task)
                except Exception:
                    pass
        return updater


@dataclass
class TaskContext:
    """任务执行上下文，传递给执行器"""
    task: Task
    guard: HardwareGuard
    check_cancel: Callable
    check_pause: Callable
    update_progress: Callable

    def is_canceled(self) -> bool:
        return self.check_cancel()

    def wait_if_paused(self) -> None:
        """如果被暂停则阻塞，直到恢复或取消"""
        while not self.check_cancel():
            if self.check_pause(timeout=0.5):
                break

    def should_throttle(self) -> bool:
        """是否需要降速"""
        return self.guard.should_throttle()
