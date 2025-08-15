#!/usr/bin/env python3
"""
Test TaskManager functionality:
1. Use task_name as identifier to prevent duplicate task submission
2. submit_task method always returns Future objects
"""

import time
import threading
from concurrent.futures import Future
from django.test import TestCase
from utils.task_manager import TaskManager


def test_function(name, duration=2):
    """Simple test function"""
    time.sleep(duration)
    return f"Result from {name}"


def test_task(duration=1):
    """Test task function"""
    time.sleep(duration)
    return f"Task completed after {duration} seconds"


class TaskManagerTestCase(TestCase):
    """TaskManager test cases"""

    def test_duplicate_task_prevention(self):
        """Test duplicate task prevention functionality"""
        tm = TaskManager(max_workers=3)

        # Submit first task
        future1 = tm.submit_task(
            "test_task", test_function, "first", 1
        )  # Reduce wait time
        self.assertIsNotNone(
            future1, "First task submission should return Future object"
        )

        # Immediately try to submit same-name task (while first task is still running)
        future2 = tm.submit_task("test_task", test_function, "second", 1)
        self.assertIs(
            future1, future2, "Same-name task should return the same Future object"
        )

        # Check task status
        status = tm.get_task_status("test_task")
        self.assertIsNotNone(status, "Should be able to get task status")
        self.assertEqual(status["name"], "test_task", "Task name should be correct")
        self.assertIn(
            status["status"],
            ["running", "completed"],
            "Task status should be running or completed",
        )

        # Wait for first task to complete
        result1 = future1.result()
        self.assertEqual(result1, "Result from first", "Task result should be correct")

        # Submit same-name task again after completion
        time.sleep(0.1)  # Ensure task is cleaned up
        future3 = tm.submit_task("test_task", test_function, "third", 1)
        self.assertIsNotNone(
            future3, "Should be able to create new same-name task after completion"
        )
        self.assertIsNot(
            future1, future3, "New task should be a different Future object"
        )

        result3 = future3.result()
        self.assertEqual(
            result3, "Result from third", "New task result should be correct"
        )

        # Check task list
        all_tasks = tm.list_tasks()
        self.assertIn("test_task", all_tasks, "Task list should contain the task")
        self.assertEqual(
            all_tasks["test_task"]["status"],
            "completed",
            "Task status should be completed",
        )

    def test_concurrent_different_tasks(self):
        """Test concurrent execution of different named tasks"""
        tm = TaskManager(max_workers=3)

        # Submit multiple different named tasks
        task_count = 3
        tasks = []
        for i in range(task_count):
            task_name = f"concurrent_task_{i}"
            future = tm.submit_task(
                task_name, test_function, f"task_{i}", 1
            )  # Reduce wait time
            tasks.append((task_name, future))
            self.assertIsNotNone(
                future, f"Task {task_name} should be submitted successfully"
            )

        # Verify all tasks are different Future objects
        futures = [future for _, future in tasks]
        unique_futures = set(id(f) for f in futures)
        self.assertEqual(
            len(unique_futures),
            task_count,
            "All tasks should be different Future objects",
        )

        # Wait for all tasks to complete and verify results
        results = []
        for i, (task_name, future) in enumerate(tasks):
            result = future.result()
            expected_result = f"Result from task_{i}"
            self.assertEqual(
                result, expected_result, f"Task {task_name} result should be correct"
            )
            results.append(result)

        # Verify all results are different
        self.assertEqual(
            len(set(results)), task_count, "All task results should be different"
        )

        # Check task list
        all_tasks = tm.list_tasks()
        for i in range(task_count):
            task_name = f"concurrent_task_{i}"
            self.assertIn(task_name, all_tasks, f"Task list should contain {task_name}")
            self.assertEqual(
                all_tasks[task_name]["status"],
                "completed",
                f"{task_name} status should be completed",
            )

    def test_submit_task_always_returns_future(self):
        """Test that submit_task always returns Future objects"""
        tm = TaskManager(max_workers=2)

        # Test 1: Submit new task
        future1 = tm.submit_task("test_task_1", test_task, 0.3)
        self.assertIsNotNone(future1, "New task should return Future object")

        # Test 2: Submit same-name task (while task is still running)
        future2 = tm.submit_task("test_task_1", test_task, 0.3)
        self.assertIsNotNone(
            future2, "Same-name task should return existing Future object"
        )
        self.assertIs(future1, future2, "Should return the same Future object")

        # Wait for task completion
        result1 = future1.result()
        self.assertIn(
            "0.3 seconds", result1, "Task result should contain correct execution time"
        )

        # Test 3: Submit same-name task again after completion
        time.sleep(0.1)  # Ensure task is completed
        future3 = tm.submit_task("test_task_1", test_task, 0.2)
        self.assertIsNotNone(
            future3, "Same-name task after completion should create new Future object"
        )
        self.assertIsNot(future3, future1, "Should be a new Future object")

        result3 = future3.result()
        self.assertIn(
            "0.2 seconds",
            result3,
            "New task result should contain correct execution time",
        )

        # Test 4: Concurrent submission of multiple same-name tasks
        futures_list = []

        def submit_concurrent_task():
            future = tm.submit_task("concurrent_task", test_task, 0.5)
            futures_list.append(future)

        # Start multiple threads to submit same-name tasks concurrently
        threads = []
        thread_count = 3

        for i in range(thread_count):
            thread = threading.Thread(target=submit_concurrent_task)
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Verify all threads got Future objects
        self.assertEqual(
            len(futures_list),
            thread_count,
            f"Should have {thread_count} Future objects",
        )
        self.assertTrue(
            all(f is not None for f in futures_list), "All Futures should not be None"
        )

        # Check if they all point to the same Future (because they are same-name tasks)
        unique_futures = set(id(f) for f in futures_list if f is not None)
        self.assertEqual(
            len(unique_futures),
            1,
            "All same-name tasks should point to the same Future object",
        )

        # Wait for concurrent task completion
        if futures_list and futures_list[0]:
            result = futures_list[0].result()
            self.assertIn(
                "0.5 seconds",
                result,
                "Concurrent task result should contain correct execution time",
            )
