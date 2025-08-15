import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict

logger = logging.getLogger(__name__)


class TaskManager:
    """
    简易任务管理系统，用于管理后台任务
    全局单例模式，整个Django进程共享一个实例
    """

    def __init__(self, max_workers=5, max_task_history=1000, restart_threshold=200):
        self.max_workers = max_workers
        self.max_task_history = max_task_history
        self.restart_threshold = restart_threshold
        self.tasks_executed_since_restart = 0
        self.executor = ThreadPoolExecutor(
            max_workers=self.max_workers, thread_name_prefix="task_manager_"
        )
        self.tasks = OrderedDict()  # 使用有序字典，便于清理最老任务
        self.futures = {}  # 存储任务名到Future的映射
        self.lock = threading.Lock()  # 线程安全锁

    def _restart_executor(self):
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

    def submit_task(self, task_name, task_fn, *args, **kwargs):
        """提交新任务，始终返回Future对象"""
        with self.lock:
            # 检查是否已存在同名任务
            if task_name in self.tasks:
                existing_status = self.tasks[task_name]["status"]
                if existing_status in ("pending", "running"):
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
            "id": task_name,  # 使用task_name作为id
            "name": task_name,
            "status": "pending",  # 任务状态: pending/running/completed/failed
            "result": None,
            "exception": None,
            "progress": 0,
            "finish_time": None,
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

    def _cleanup_tasks(self, max_age_seconds=3600):
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
                if task["status"] in ("completed", "failed")
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

    def _wrap_task(self, task_name, task_fn, *args, **kwargs):
        """包装任务函数，添加状态管理"""
        try:
            with self.lock:
                self.tasks[task_name]["status"] = "running"
            result = task_fn(*args, **kwargs)
            with self.lock:
                self.tasks[task_name]["result"] = result
                self.tasks[task_name]["status"] = "completed"
                self.tasks[task_name]["finish_time"] = time.time()
            return result
        except Exception as e:
            with self.lock:
                self.tasks[task_name]["exception"] = str(e)
                self.tasks[task_name]["status"] = "failed"
                self.tasks[task_name]["finish_time"] = time.time()
            logger.exception(f"Task {task_name} failed: {str(e)}")
            raise
        finally:
            # 每次任务结束后尝试清理历史任务
            self._cleanup_tasks()

    def _make_done_callback(self, task_name):
        def callback(future):
            try:
                future.result()
            except Exception:
                pass

        return callback

    def get_task_status(self, task_name):
        with self.lock:
            return self.tasks.get(task_name, {}).copy()

    def list_tasks(self, filter_status=None):
        with self.lock:
            if filter_status:
                return {
                    k: v for k, v in self.tasks.items() if v["status"] == filter_status
                }
            return self.tasks.copy()

    def update_progress(self, task_name, progress):
        with self.lock:
            if task_name in self.tasks and 0 <= progress <= 100:
                self.tasks[task_name]["progress"] = progress


# 全局任务管理器实例（单例）
task_manager = TaskManager(max_workers=10)
