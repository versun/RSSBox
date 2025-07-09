import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict


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
        self.lock = threading.Lock()  # 线程安全锁

    def _restart_executor(self):
        """
        Safely restarts the ThreadPoolExecutor to prevent memory leaks.
        This method shuts down the current executor, waits for active tasks to complete,
        and then creates a new executor.
        """
        logging.info(
            f"Task manager: Restarting worker threads. Tasks executed: {self.tasks_executed_since_restart}"
        )
        # Shutdown the existing executor
        self.executor.shutdown(wait=True)

        # Create a new one
        self.executor = ThreadPoolExecutor(
            max_workers=self.max_workers, thread_name_prefix="task_manager_"
        )
        # Reset the counter
        self.tasks_executed_since_restart = 0
        logging.info("Task manager: Worker threads restarted successfully.")

    def submit_task(self, task_name, task_fn, *args, **kwargs):
        """提交新任务"""
        with self.lock:
            if self.tasks_executed_since_restart >= self.restart_threshold:
                self._restart_executor()
            self.tasks_executed_since_restart += 1

        self._cleanup_tasks()
        task_id = str(uuid.uuid4())
        task_info = {
            "id": task_id,
            "name": task_name,
            "status": "pending",  # 任务状态: pending/running/completed/failed
            "result": None,
            "exception": None,
            "progress": 0,
            "finish_time": None,
        }
        with self.lock:
            self.tasks[task_id] = task_info
        future = self.executor.submit(
            self._wrap_task, task_id, task_fn, *args, **kwargs
        )
        future.add_done_callback(self._make_done_callback(task_id))
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

            # 2. 按数量上限清理
            while len(self.tasks) > self.max_task_history:
                self.tasks.popitem(last=False)

    def _wrap_task(self, task_id, task_fn, *args, **kwargs):
        """包装任务函数，添加状态管理"""
        try:
            with self.lock:
                self.tasks[task_id]["status"] = "running"
            result = task_fn(*args, **kwargs)
            with self.lock:
                self.tasks[task_id]["result"] = result
                self.tasks[task_id]["status"] = "completed"
                self.tasks[task_id]["finish_time"] = time.time()
            return result
        except Exception as e:
            with self.lock:
                self.tasks[task_id]["exception"] = str(e)
                self.tasks[task_id]["status"] = "failed"
                self.tasks[task_id]["finish_time"] = time.time()
            logging.exception(f"Task {task_id} failed: {str(e)}")
            raise
        finally:
            # 每次任务结束后尝试清理历史任务
            self._cleanup_tasks()

    def _make_done_callback(self, task_id):
        def callback(future):
            try:
                future.result()
            except Exception:
                pass

        return callback

    def get_task_status(self, task_id):
        with self.lock:
            return self.tasks.get(task_id, {}).copy()

    def list_tasks(self, filter_status=None):
        with self.lock:
            if filter_status:
                return {
                    k: v for k, v in self.tasks.items() if v["status"] == filter_status
                }
            return self.tasks.copy()

    def update_progress(self, task_id, progress):
        with self.lock:
            if task_id in self.tasks and 0 <= progress <= 100:
                self.tasks[task_id]["progress"] = progress


# 全局任务管理器实例（单例）
task_manager = TaskManager(max_workers=10)
