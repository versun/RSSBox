import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from collections import OrderedDict
from typing import Dict, Any, Optional, Callable, Union, List
from enum import Enum

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """任务状态枚举"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskManager:
    """
    简易任务管理系统，用于管理后台任务
    全局单例模式，整个Django进程共享一个实例
    """

    def __init__(
        self,
        max_workers: int = 5,
        max_task_history: int = 1000,
        restart_threshold: int = 200,
    ):
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        if max_task_history <= 0:
            raise ValueError("max_task_history must be positive")
        if restart_threshold <= 0:
            raise ValueError("restart_threshold must be positive")

        self.max_workers = max_workers
        self.max_task_history = max_task_history
        self.restart_threshold = restart_threshold
        self.tasks_executed_since_restart = 0
        self.executor = ThreadPoolExecutor(
            max_workers=self.max_workers, thread_name_prefix="task_manager_"
        )
        self.tasks: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self.futures: Dict[str, Future] = {}
        self.lock = threading.RLock()  # 使用可重入锁，提高性能
        self._shutdown = False

    def _restart_executor(self) -> None:
        """
        Safely restarts the ThreadPoolExecutor to prevent memory leaks.
        This method shuts down the current executor, waits for active tasks to complete,
        and then creates a new executor.
        """
        logger.info(
            f"Task manager: Restarting worker threads. Tasks executed: {self.tasks_executed_since_restart}"
        )
        # Shutdown the existing executor
        self.executor.shutdown(wait=True)

        # Create a new one
        self.executor = ThreadPoolExecutor(
            max_workers=self.max_workers, thread_name_prefix="task_manager_"
        )
        # Clear futures mapping since old futures are no longer valid
        self.futures.clear()
        # Reset the counter
        self.tasks_executed_since_restart = 0
        logger.info("Task manager: Worker threads restarted successfully.")

    def submit_task(self, task_name: str, task_fn: Callable, *args, **kwargs) -> Future:
        """提交新任务，始终返回Future对象"""
        if not task_name or not isinstance(task_name, str):
            raise ValueError("task_name must be a non-empty string")
        if not callable(task_fn):
            raise ValueError("task_fn must be callable")
        if self._shutdown:
            raise RuntimeError("TaskManager is shutdown")

        with self.lock:
            # 检查是否已存在同名任务
            if task_name in self.tasks:
                existing_status = self.tasks[task_name]["status"]
                if existing_status in (
                    TaskStatus.PENDING.value,
                    TaskStatus.RUNNING.value,
                ):
                    logger.warning(
                        f"Task '{task_name}' already exists with status '{existing_status}', returning existing future"
                    )
                    # 返回已存在的Future
                    return self.futures.get(task_name)

            if self.tasks_executed_since_restart >= self.restart_threshold:
                self._restart_executor()
            self.tasks_executed_since_restart += 1
            logger.debug(
                f"Submitting task: {task_name} (Total executed: {self.tasks_executed_since_restart})"
            )

        self._cleanup_tasks()
        task_info = {
            "id": task_name,
            "name": task_name,
            "status": TaskStatus.PENDING.value,
            "result": None,
            "exception": None,
            "progress": 0,
            "start_time": time.time(),
            "finish_time": None,
            "args": str(args),
            "kwargs": str(kwargs),
        }

        with self.lock:
            self.tasks[task_name] = task_info

        future = self.executor.submit(
            self._wrap_task, task_name, task_fn, *args, **kwargs
        )
        future.add_done_callback(self._make_done_callback(task_name))

        # 存储Future映射
        with self.lock:
            self.futures[task_name] = future

        return future

    def _cleanup_tasks(self, max_age_seconds: int = 3600) -> None:
        """
        清理任务列表:
        1. 移除所有超过 max_age_seconds 的已完成或失败的任务。
        2. 如果任务总数仍然超过 max_task_history，则从最旧的任务开始移除，直到满足数量限制。
        """
        with self.lock:
            # 1. 按时间清理
            now = time.time()
            to_delete = [
                task_id
                for task_id, task in self.tasks.items()
                if task["status"]
                in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value)
                and task["finish_time"]
                and now - task["finish_time"] > max_age_seconds
            ]
            for task_id in to_delete:
                del self.tasks[task_id]
                # 同时清理对应的Future映射
                self.futures.pop(task_id, None)

            # 2. 按数量上限清理
            while len(self.tasks) > self.max_task_history:
                task_id, _ = self.tasks.popitem(last=False)
                # 同时清理对应的Future映射
                self.futures.pop(task_id, None)

    def _wrap_task(self, task_name: str, task_fn: Callable, *args, **kwargs) -> Any:
        """包装任务函数，添加状态管理"""
        try:
            with self.lock:
                if task_name in self.tasks:
                    self.tasks[task_name]["status"] = TaskStatus.RUNNING.value

            result = task_fn(*args, **kwargs)

            with self.lock:
                if task_name in self.tasks:
                    self.tasks[task_name]["result"] = result
                    self.tasks[task_name]["status"] = TaskStatus.COMPLETED.value
                    self.tasks[task_name]["finish_time"] = time.time()

            return result
        except Exception as e:
            with self.lock:
                if task_name in self.tasks:
                    self.tasks[task_name]["exception"] = str(e)
                    self.tasks[task_name]["status"] = TaskStatus.FAILED.value
                    self.tasks[task_name]["finish_time"] = time.time()

            logger.exception(f"Task {task_name} failed: {str(e)}")
            raise
        finally:
            # 每次任务结束后尝试清理历史任务
            self._cleanup_tasks()

    def _make_done_callback(self, task_name: str) -> Callable[[Future], None]:
        def callback(future: Future) -> None:
            try:
                future.result()
            except Exception:
                pass

        return callback

    def get_task_status(self, task_name: str) -> Dict[str, Any]:
        """获取任务状态"""
        if not task_name:
            return {}
        with self.lock:
            return self.tasks.get(task_name, {}).copy()

    def list_tasks(
        self, filter_status: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """列出所有任务，可选择性过滤状态"""
        with self.lock:
            if filter_status:
                return {
                    k: v for k, v in self.tasks.items() if v["status"] == filter_status
                }
            return self.tasks.copy()

    def update_progress(self, task_name: str, progress: int) -> bool:
        """更新任务进度"""
        if not task_name or not isinstance(progress, int):
            return False

        with self.lock:
            if task_name in self.tasks and 0 <= progress <= 100:
                self.tasks[task_name]["progress"] = progress
                return True
        return False

    def cancel_task(self, task_name: str) -> bool:
        """取消任务"""
        if not task_name:
            return False

        with self.lock:
            if task_name not in self.tasks:
                return False

            task = self.tasks[task_name]
            if task["status"] in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value):
                return False

            # 尝试取消Future
            future = self.futures.get(task_name)
            if future and not future.done():
                cancelled = future.cancel()
                if cancelled:
                    task["status"] = TaskStatus.CANCELLED.value
                    task["finish_time"] = time.time()
                    return True

        return False

    def get_task_count(self, status: Optional[str] = None) -> int:
        """获取任务数量"""
        with self.lock:
            if status:
                return sum(
                    1 for task in self.tasks.values() if task["status"] == status
                )
            return len(self.tasks)

    def get_running_tasks(self) -> List[str]:
        """获取正在运行的任务列表"""
        with self.lock:
            return [
                task_name
                for task_name, task in self.tasks.items()
                if task["status"] == TaskStatus.RUNNING.value
            ]

    def get_pending_tasks(self) -> List[str]:
        """获取等待中的任务列表"""
        with self.lock:
            return [
                task_name
                for task_name, task in self.tasks.items()
                if task["status"] == TaskStatus.PENDING.value
            ]

    def clear_completed_tasks(self) -> int:
        """清理所有已完成的任务，返回清理数量"""
        with self.lock:
            to_delete = [
                task_id
                for task_id, task in self.tasks.items()
                if task["status"]
                in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value)
            ]
            for task_id in to_delete:
                del self.tasks[task_id]
                self.futures.pop(task_id, None)
            return len(to_delete)

    def shutdown(self, wait: bool = True) -> None:
        """关闭任务管理器"""
        if self._shutdown:
            return

        self._shutdown = True
        logger.info("TaskManager shutting down...")

        with self.lock:
            # 取消所有未完成的任务
            for task_name, future in self.futures.items():
                if not future.done():
                    future.cancel()

        # 关闭执行器
        self.executor.shutdown(wait=wait)
        logger.info("TaskManager shutdown complete")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown()


# 全局任务管理器实例（单例）
task_manager = TaskManager(max_workers=10)
