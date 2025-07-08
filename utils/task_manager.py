import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor


class TaskManager:
    """
    简易任务管理系统，用于管理后台任务
    全局单例模式，整个Django进程共享一个实例
    """

    def __init__(self, max_workers=5, max_task_history=1000):
        self.max_task_history = max_task_history
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="task_manager_"
        )
        self.tasks = {}  # 存储所有任务 {task_id: task_info}
        self.lock = threading.Lock()  # 线程安全锁

    def submit_task(self, task_name, task_fn, *args, **kwargs):
        """提交新任务"""
        # 清理旧任务
        if len(self.tasks) >= self.max_task_history:
            self.cleanup_old_tasks()

        task_id = str(uuid.uuid4())

        # 创建任务信息字典
        task_info = {
            "id": task_id,
            "name": task_name,
            "status": "pending",  # 任务状态: pending/running/completed/failed
            "result": None,
            "exception": None,
            "progress": 0,
        }

        # 添加到任务字典
        with self.lock:
            self.tasks[task_id] = task_info

        # 提交任务到线程池
        future = self.executor.submit(
            self._wrap_task, task_id, task_fn, *args, **kwargs
        )
        future.add_done_callback(self._make_done_callback(task_id))

        return task_id

    def cleanup_old_tasks(self, max_age_seconds=3600):
        now = time.time()
        for task_id in list(self.tasks.keys()):
            task = self.tasks[task_id]
            if task["status"] in ("completed", "failed"):
                if now - task.get("finish_time", now) > max_age_seconds:
                    del self.tasks[task_id]
                    
    def _wrap_task(self, task_id, task_fn, *args, **kwargs):
        """包装任务函数，添加状态管理"""
        try:
            with self.lock:
                self.tasks[task_id]["status"] = "running"

            # 执行实际任务
            result = task_fn(*args, **kwargs)

            with self.lock:
                self.tasks[task_id]["result"] = result
                self.tasks[task_id]["status"] = "completed"

            return result
        except Exception as e:
            with self.lock:
                self.tasks[task_id]["exception"] = str(e)
                self.tasks[task_id]["status"] = "failed"
            logging.exception(f"Task {task_id} failed: {str(e)}")
            raise

    def _make_done_callback(self, task_id):
        """创建任务完成回调函数"""

        def callback(future):
            try:
                # 获取任务结果（如果有异常会在这里抛出）
                future.result()
            except Exception:
                # 异常已在_wrap_task中处理，这里只做日志记录
                pass

        return callback

    def get_task_status(self, task_id):
        """获取任务状态"""
        with self.lock:
            return self.tasks.get(task_id, {}).copy()

    def list_tasks(self, filter_status=None):
        """列出所有任务（可过滤）"""
        with self.lock:
            if filter_status:
                return {
                    k: v for k, v in self.tasks.items() if v["status"] == filter_status
                }
            return self.tasks.copy()

    def update_progress(self, task_id, progress):
        """更新任务进度 (0-100)"""
        with self.lock:
            if task_id in self.tasks and 0 <= progress <= 100:
                self.tasks[task_id]["progress"] = progress


# 全局任务管理器实例（单例）
task_manager = TaskManager(max_workers=10)
