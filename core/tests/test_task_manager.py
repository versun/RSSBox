#!/usr/bin/env python3
"""
Test TaskManager functionality:
1. Use task_name as identifier to prevent duplicate task submission
2. submit_task method always returns Future objects
"""

import time
from django.test import TestCase
from utils.task_manager import TaskManager


def test_function(name, duration=0.02):
    """Simple test function with minimal duration"""
    time.sleep(duration)
    return f"Result from {name}"


class TaskManagerTestCase(TestCase):
    """TaskManager test cases"""
    
    def setUp(self):
        """Set up test TaskManager instance"""
        self.tm = TaskManager(max_workers=3)

    def test_duplicate_task_prevention_and_lifecycle(self):
        """Test duplicate task prevention and task lifecycle management."""
        # Submit first task
        future1 = self.tm.submit_task("test_task", test_function, "first")
        self.assertIsNotNone(future1)

        # Submit same-name task (should return same Future)
        future2 = self.tm.submit_task("test_task", test_function, "second")
        self.assertIs(future1, future2)

        # Check task status
        status = self.tm.get_task_status("test_task")
        self.assertEqual(status["name"], "test_task")
        self.assertIn(status["status"], ["pending", "running", "completed"])

        # Wait for completion and verify result
        result1 = future1.result()
        self.assertEqual(result1, "Result from first")

        # Submit same-name task after completion (should create new Future)
        time.sleep(0.05)  # Brief cleanup wait
        future3 = self.tm.submit_task("test_task", test_function, "third")
        self.assertIsNotNone(future3)
        self.assertIsNot(future1, future3)
        
        result3 = future3.result()
        self.assertEqual(result3, "Result from third")

        # Verify task list contains completed task
        all_tasks = self.tm.list_tasks()
        self.assertIn("test_task", all_tasks)
        self.assertEqual(all_tasks["test_task"]["status"], "completed")

    def test_concurrent_different_tasks(self):
        """Test concurrent execution of different named tasks."""
        # Submit multiple different named tasks
        task_count = 3
        tasks = []
        for i in range(task_count):
            task_name = f"concurrent_task_{i}"
            future = self.tm.submit_task(task_name, test_function, f"task_{i}")
            tasks.append((task_name, future))
            self.assertIsNotNone(future)

        # Verify all tasks have different Future objects
        futures = [future for _, future in tasks]
        unique_futures = set(id(f) for f in futures)
        self.assertEqual(len(unique_futures), task_count)

        # Wait for completion and verify results
        for i, (task_name, future) in enumerate(tasks):
            result = future.result()
            self.assertEqual(result, f"Result from task_{i}")

        # Verify all tasks completed successfully
        all_tasks = self.tm.list_tasks()
        for i in range(task_count):
            task_name = f"concurrent_task_{i}"
            self.assertIn(task_name, all_tasks)
            self.assertEqual(all_tasks[task_name]["status"], "completed")

    def test_future_return_behavior_and_threading(self):
        """Test Future return behavior and basic threading scenarios."""
        # Test basic Future return
        future1 = self.tm.submit_task("basic_task", test_function, "test")
        self.assertIsNotNone(future1)
        
        # Test same-name task returns same Future while running
        future2 = self.tm.submit_task("basic_task", test_function, "test2")
        self.assertIs(future1, future2)
        
        # Wait for completion
        result = future1.result()
        self.assertEqual(result, "Result from test")
        
        # Test new task after completion creates new Future
        time.sleep(0.05)
        future3 = self.tm.submit_task("basic_task", test_function, "test3")
        self.assertIsNotNone(future3)
        self.assertIsNot(future1, future3)
        
        result3 = future3.result()
        self.assertEqual(result3, "Result from test3")
        
        # Test task status and list functionality
        status = self.tm.get_task_status("basic_task")
        self.assertEqual(status["status"], "completed")
        
        all_tasks = self.tm.list_tasks()
        self.assertIn("basic_task", all_tasks)
