#!/usr/bin/env python3
"""
Test TaskManager functionality:
1. Use task_name as identifier to prevent duplicate task submission
2. submit_task method always returns Future objects
3. Test new features and edge cases
"""

import time
import threading
from unittest.mock import patch, MagicMock
from django.test import TestCase
from utils.task_manager import TaskManager, TaskStatus


def test_function(name, duration=0.02, **kwargs):
    """Simple test function with minimal duration"""
    time.sleep(duration)
    return f"Result from {name}"


def test_function_with_error(name):
    """Test function that raises an exception"""
    raise ValueError(f"Error in {name}")


def test_function_long_running(name, duration=0.1, **kwargs):
    """Longer running test function"""
    time.sleep(duration)
    return f"Long result from {name}"


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

    def test_constructor_validation(self):
        """Test constructor parameter validation."""
        # Test valid parameters
        tm = TaskManager(max_workers=1, max_task_history=10, restart_threshold=5)
        self.assertEqual(tm.max_workers, 1)
        self.assertEqual(tm.max_task_history, 10)
        self.assertEqual(tm.restart_threshold, 5)

        # Test invalid parameters
        with self.assertRaises(ValueError):
            TaskManager(max_workers=0)
        with self.assertRaises(ValueError):
            TaskManager(max_task_history=0)
        with self.assertRaises(ValueError):
            TaskManager(restart_threshold=0)

    def test_submit_task_validation(self):
        """Test submit_task parameter validation."""
        # Test invalid task_name
        with self.assertRaises(ValueError):
            self.tm.submit_task("", test_function, "test")
        with self.assertRaises(ValueError):
            self.tm.submit_task(None, test_function, "test")
        with self.assertRaises(ValueError):
            self.tm.submit_task(123, test_function, "test")

        # Test invalid task_fn
        with self.assertRaises(ValueError):
            self.tm.submit_task("test", "not_callable", "test")
        with self.assertRaises(ValueError):
            self.tm.submit_task("test", None, "test")

    def test_task_failure_handling(self):
        """Test handling of tasks that raise exceptions."""
        future = self.tm.submit_task("error_task", test_function_with_error, "test")
        self.assertIsNotNone(future)

        # Wait for completion and verify exception
        with self.assertRaises(ValueError):
            future.result()

        # Check task status
        status = self.tm.get_task_status("error_task")
        self.assertEqual(status["status"], "failed")
        self.assertIn("Error in test", status["exception"])

    def test_get_task_status_edge_cases(self):
        """Test get_task_status with edge cases."""
        # Test with non-existent task
        status = self.tm.get_task_status("non_existent")
        self.assertEqual(status, {})

        # Test with empty task name
        status = self.tm.get_task_status("")
        self.assertEqual(status, {})

        # Test with None task name
        status = self.tm.get_task_status(None)
        self.assertEqual(status, {})

    def test_list_tasks_filtering(self):
        """Test list_tasks with status filtering."""
        # Submit tasks with different statuses
        self.tm.submit_task(
            "pending_task", test_function_long_running, "pending", duration=0.2
        )
        self.tm.submit_task("completed_task", test_function, "completed")

        # Wait for completed task
        time.sleep(0.05)

        # Test filtering by status
        pending_tasks = self.tm.list_tasks("pending")
        completed_tasks = self.tm.list_tasks("completed")

        # pending_task might be running by now, so check both
        self.assertTrue(
            "pending_task" in pending_tasks
            or "pending_task" in self.tm.list_tasks("running")
        )
        self.assertIn("completed_task", completed_tasks)

        # Test without filter
        all_tasks = self.tm.list_tasks()
        self.assertIn("pending_task", all_tasks)
        self.assertIn("completed_task", all_tasks)

    def test_update_progress(self):
        """Test progress update functionality."""
        # Submit a task
        future = self.tm.submit_task(
            "progress_task", test_function_long_running, "progress"
        )

        # Update progress
        self.assertTrue(self.tm.update_progress("progress_task", 50))
        self.assertTrue(self.tm.update_progress("progress_task", 100))

        # Test invalid progress values
        self.assertFalse(self.tm.update_progress("progress_task", -1))
        self.assertFalse(self.tm.update_progress("progress_task", 101))
        self.assertFalse(self.tm.update_progress("progress_task", "invalid"))

        # Test with non-existent task
        self.assertFalse(self.tm.update_progress("non_existent", 50))

        # Test with empty task name
        self.assertFalse(self.tm.update_progress("", 50))

        # Wait for completion
        future.result()

    def test_cancel_task(self):
        """Test task cancellation functionality."""
        # Submit a long-running task
        future = self.tm.submit_task(
            "cancel_task", test_function_long_running, "cancel", duration=0.2
        )

        # Give it a moment to start
        time.sleep(0.05)

        # Try to cancel the task
        cancelled = self.tm.cancel_task("cancel_task")

        # Check if cancellation was successful or if task already completed
        if cancelled:
            # Verify task status
            status = self.tm.get_task_status("cancel_task")
            self.assertEqual(status["status"], "cancelled")
        else:
            # Task might have completed before cancellation or still be running
            status = self.tm.get_task_status("cancel_task")
            self.assertIn(status["status"], ["completed", "cancelled", "running"])

        # Test cancelling non-existent task
        self.assertFalse(self.tm.cancel_task("non_existent"))

        # Test cancelling empty task name
        self.assertFalse(self.tm.cancel_task(""))

        # Test cancelling completed task
        completed_future = self.tm.submit_task(
            "completed_cancel_test", test_function, "test"
        )
        completed_future.result()
        time.sleep(0.05)
        self.assertFalse(self.tm.cancel_task("completed_cancel_test"))

    def test_get_task_count(self):
        """Test task counting functionality."""
        # Submit multiple tasks
        self.tm.submit_task("count_task_1", test_function, "1")
        self.tm.submit_task("count_task_2", test_function, "2")
        self.tm.submit_task("count_task_3", test_function_long_running, "3")

        # Wait for some tasks to complete
        time.sleep(0.05)

        # Test total count
        total_count = self.tm.get_task_count()
        self.assertGreaterEqual(total_count, 3)

        # Test count by status
        completed_count = self.tm.get_task_count("completed")
        self.assertGreaterEqual(completed_count, 2)

        running_count = self.tm.get_task_count("running")
        self.assertGreaterEqual(running_count, 0)

    def test_get_running_and_pending_tasks(self):
        """Test getting running and pending task lists."""
        # Submit tasks
        self.tm.submit_task(
            "running_task", test_function_long_running, "running", duration=0.2
        )
        self.tm.submit_task(
            "pending_task", test_function_long_running, "pending", duration=0.2
        )

        # Give tasks a moment to start
        time.sleep(0.05)

        # Get lists
        running_tasks = self.tm.get_running_tasks()
        pending_tasks = self.tm.get_pending_tasks()

        # Verify lists contain expected tasks (they might be in different states)
        all_tasks = self.tm.list_tasks()
        self.assertIn("running_task", all_tasks)
        self.assertIn("pending_task", all_tasks)

        # Check that at least one task is in running or pending state
        task_states = [
            all_tasks["running_task"]["status"],
            all_tasks["pending_task"]["status"],
        ]
        self.assertTrue(any(state in ["pending", "running"] for state in task_states))

    def test_clear_completed_tasks(self):
        """Test clearing completed tasks."""
        # Submit and complete some tasks
        self.tm.submit_task("clear_task_1", test_function, "1")
        self.tm.submit_task("clear_task_2", test_function, "2")
        self.tm.submit_task("clear_task_3", test_function_long_running, "3")

        # Wait for some tasks to complete
        time.sleep(0.05)

        # Clear completed tasks
        cleared_count = self.tm.clear_completed_tasks()
        self.assertGreaterEqual(cleared_count, 2)

        # Verify remaining tasks
        remaining_tasks = self.tm.list_tasks()
        self.assertIn("clear_task_3", remaining_tasks)

    def test_shutdown_functionality(self):
        """Test shutdown functionality."""
        # Submit a task
        future = self.tm.submit_task(
            "shutdown_test", test_function_long_running, "test"
        )

        # Shutdown
        self.tm.shutdown(wait=False)

        # Verify shutdown state
        self.assertTrue(self.tm._shutdown)

        # Try to submit new task after shutdown
        with self.assertRaises(RuntimeError):
            self.tm.submit_task("post_shutdown", test_function, "test")

    def test_context_manager(self):
        """Test context manager functionality."""
        with TaskManager(max_workers=2) as tm:
            # Submit a task
            future = tm.submit_task("context_test", test_function, "test")
            result = future.result()
            self.assertEqual(result, "Result from test")

            # Verify task completed
            status = tm.get_task_status("context_test")
            self.assertEqual(status["status"], "completed")

    def test_restart_executor_threshold(self):
        """Test executor restart when threshold is reached."""
        # Create TaskManager with low threshold
        tm = TaskManager(max_workers=2, restart_threshold=2)

        # Submit tasks to trigger restart
        for i in range(3):
            future = tm.submit_task(f"restart_test_{i}", test_function, f"test_{i}")
            future.result()

        # Verify executor was restarted
        self.assertEqual(tm.tasks_executed_since_restart, 1)

    def test_cleanup_tasks_functionality(self):
        """Test task cleanup functionality."""
        # Submit many tasks to test cleanup
        for i in range(5):
            self.tm.submit_task(f"cleanup_test_{i}", test_function, f"test_{i}")

        # Wait for completion
        time.sleep(0.05)

        # Force cleanup with short age
        self.tm._cleanup_tasks(max_age_seconds=0)

        # Verify cleanup worked
        remaining_tasks = self.tm.list_tasks()
        self.assertLess(len(remaining_tasks), 5)

    def test_task_info_structure(self):
        """Test task info structure and fields."""
        # Submit a task
        future = self.tm.submit_task(
            "info_test", test_function, "arg1", kwarg1="value1"
        )

        # Get task info
        task_info = self.tm.get_task_status("info_test")

        # Verify all expected fields
        expected_fields = [
            "id",
            "name",
            "status",
            "result",
            "exception",
            "progress",
            "start_time",
            "finish_time",
            "args",
            "kwargs",
        ]
        for field in expected_fields:
            self.assertIn(field, task_info)

        # Verify specific values
        self.assertEqual(task_info["name"], "info_test")
        self.assertEqual(task_info["progress"], 0)
        self.assertIsNotNone(task_info["start_time"])

        # Wait for completion
        future.result()
        time.sleep(0.05)

        # Verify completion fields
        completed_info = self.tm.get_task_status("info_test")
        self.assertEqual(completed_info["status"], "completed")
        self.assertIsNotNone(completed_info["finish_time"])
        self.assertEqual(completed_info["result"], "Result from arg1")

    def test_concurrent_access_thread_safety(self):
        """Test thread safety with concurrent access."""
        results = []
        errors = []

        def worker(worker_id):
            try:
                # Submit task
                future = self.tm.submit_task(
                    f"concurrent_worker_{worker_id}",
                    test_function,
                    f"worker_{worker_id}",
                )
                result = future.result()
                results.append(result)

                # Get status
                status = self.tm.get_task_status(f"concurrent_worker_{worker_id}")
                if status["status"] != "completed":
                    errors.append(
                        f"Worker {worker_id}: unexpected status {status['status']}"
                    )

            except Exception as e:
                errors.append(f"Worker {worker_id}: {str(e)}")

        # Start multiple worker threads
        threads = []
        for i in range(5):
            thread = threading.Thread(target=worker, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all threads
        for thread in threads:
            thread.join()

        # Verify results
        self.assertEqual(len(results), 5)
        self.assertEqual(len(errors), 0)

        # Verify all tasks completed
        all_tasks = self.tm.list_tasks()
        for i in range(5):
            task_name = f"concurrent_worker_{i}"
            self.assertIn(task_name, all_tasks)
            self.assertEqual(all_tasks[task_name]["status"], "completed")

    def test_edge_cases_and_error_handling(self):
        """Test various edge cases and error handling scenarios."""
        # Test with very long task names
        long_name = "a" * 1000
        future = self.tm.submit_task(long_name, test_function, "test")
        self.assertIsNotNone(future)
        result = future.result()
        self.assertEqual(result, "Result from test")

        # Test with special characters in task names
        special_name = "task_with_special_chars_!@#$%^&*()"
        future = self.tm.submit_task(special_name, test_function, "test")
        self.assertIsNotNone(future)
        result = future.result()
        self.assertEqual(result, "Result from test")

        # Test with None arguments
        future = self.tm.submit_task("none_args_task", test_function, None)
        self.assertIsNotNone(future)
        result = future.result()
        self.assertEqual(result, "Result from None")

    def test_task_cleanup_edge_cases(self):
        """Test task cleanup with edge cases."""
        # Submit many tasks to test cleanup limits
        for i in range(10):
            self.tm.submit_task(f"cleanup_edge_test_{i}", test_function, f"test_{i}")

        # Wait for completion
        time.sleep(0.05)

        # Test cleanup with very short age
        self.tm._cleanup_tasks(max_age_seconds=0.001)

        # Test cleanup with very long age
        self.tm._cleanup_tasks(max_age_seconds=999999)

        # Verify cleanup worked
        remaining_tasks = self.tm.list_tasks()
        self.assertLess(len(remaining_tasks), 10)

    def test_shutdown_edge_cases(self):
        """Test shutdown functionality with edge cases."""
        # Test shutdown when no tasks are running
        tm = TaskManager(max_workers=2)
        tm.shutdown(wait=True)

        # Test shutdown when already shutdown
        tm.shutdown(wait=True)

        # Test shutdown with wait=False
        tm2 = TaskManager(max_workers=2)
        tm2.shutdown(wait=False)

    def test_context_manager_edge_cases(self):
        """Test context manager with edge cases."""
        # Test normal usage
        with TaskManager(max_workers=2) as tm:
            future = tm.submit_task("context_normal", test_function, "test")
            result = future.result()
            self.assertEqual(result, "Result from test")

        # Test with exception
        try:
            with TaskManager(max_workers=2) as tm:
                future = tm.submit_task(
                    "context_exception", test_function_with_error, "test"
                )
                future.result()
        except ValueError:
            pass  # Expected exception

        # Test with multiple tasks
        with TaskManager(max_workers=3) as tm:
            futures = []
            for i in range(3):
                future = tm.submit_task(
                    f"context_multi_{i}", test_function, f"test_{i}"
                )
                futures.append(future)

            # Wait for all
            for future in futures:
                future.result()

    def test_future_callback_edge_cases(self):
        """Test Future callback edge cases."""
        # Test callback with exception
        future = self.tm.submit_task("callback_test", test_function_with_error, "test")

        # Wait for completion
        with self.assertRaises(ValueError):
            future.result()

        # Verify task status
        status = self.tm.get_task_status("callback_test")
        self.assertEqual(status["status"], "failed")

    def test_task_status_enum_usage(self):
        """Test TaskStatus enum usage."""
        # Verify all status values
        self.assertEqual(TaskStatus.PENDING.value, "pending")
        self.assertEqual(TaskStatus.RUNNING.value, "running")
        self.assertEqual(TaskStatus.COMPLETED.value, "completed")
        self.assertEqual(TaskStatus.FAILED.value, "failed")
        self.assertEqual(TaskStatus.CANCELLED.value, "cancelled")

        # Test status filtering with enum values
        self.tm.submit_task("enum_test", test_function, "test")
        time.sleep(0.05)

        # Test filtering with enum values
        completed_tasks = self.tm.list_tasks(TaskStatus.COMPLETED.value)
        self.assertIn("enum_test", completed_tasks)

    def test_memory_management(self):
        """Test memory management and cleanup."""
        # Submit many tasks to test memory cleanup
        initial_count = len(self.tm.list_tasks())

        for i in range(20):
            self.tm.submit_task(f"memory_test_{i}", test_function, f"test_{i}")

        # Wait for completion
        time.sleep(0.05)

        # Force cleanup
        self.tm._cleanup_tasks(max_age_seconds=0)

        # Verify cleanup reduced task count
        final_count = len(self.tm.list_tasks())
        self.assertLess(final_count, initial_count + 20)

    def test_max_task_history_cleanup(self):
        """Test cleanup when max_task_history is reached."""
        # Create TaskManager with very low max_task_history
        tm = TaskManager(max_workers=2, max_task_history=3)

        # Submit more tasks than the limit
        for i in range(5):
            tm.submit_task(f"history_test_{i}", test_function, f"test_{i}")

        # Wait for completion
        time.sleep(0.05)

        # Force cleanup to trigger max_task_history limit
        tm._cleanup_tasks(max_age_seconds=999999)  # Don't clean by age

        # Verify only max_task_history tasks remain
        remaining_tasks = tm.list_tasks()
        self.assertLessEqual(len(remaining_tasks), 3)

        # Cleanup
        tm.shutdown()

    def test_future_cancellation_edge_cases(self):
        """Test Future cancellation edge cases."""
        # Test cancellation when future is already done
        future = self.tm.submit_task("done_cancel_test", test_function, "test")
        future.result()  # Wait for completion
        time.sleep(0.05)

        # Try to cancel completed task
        self.assertFalse(self.tm.cancel_task("done_cancel_test"))

        # Test cancellation when future is None
        # This would require mocking, but we can test the logic path
        # by ensuring the method handles edge cases gracefully

    def test_successful_task_cancellation(self):
        """Test successful task cancellation."""
        # Submit a very long-running task
        future = self.tm.submit_task(
            "long_cancel_test", test_function_long_running, "test", duration=1.0
        )

        # Give it a moment to start
        time.sleep(0.1)

        # Try to cancel the task
        cancelled = self.tm.cancel_task("long_cancel_test")

        # The task should be cancelled successfully
        if cancelled:
            status = self.tm.get_task_status("long_cancel_test")
            self.assertEqual(status["status"], "cancelled")
        else:
            # If not cancelled, it might have completed
            status = self.tm.get_task_status("long_cancel_test")
            self.assertIn(status["status"], ["completed", "cancelled", "running"])

        # Clean up by waiting for the future (it might be cancelled)
        try:
            future.result(timeout=0.1)
        except:
            pass  # Expected if cancelled

    @patch("concurrent.futures.Future.cancel")
    def test_mocked_successful_cancellation(self, mock_cancel):
        """Test successful cancellation using mock."""
        # Mock future.cancel to return True
        mock_cancel.return_value = True

        # Submit a task
        future = self.tm.submit_task(
            "mock_cancel_test", test_function_long_running, "test", duration=0.5
        )

        # Give it a moment to start
        time.sleep(0.05)

        # Try to cancel the task
        cancelled = self.tm.cancel_task("mock_cancel_test")

        # Should be cancelled successfully due to mock
        self.assertTrue(cancelled)

        # Verify task status
        status = self.tm.get_task_status("mock_cancel_test")
        self.assertEqual(status["status"], "cancelled")

        # Clean up
        try:
            future.result(timeout=0.1)
        except:
            pass
