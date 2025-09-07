from django.core.management.base import BaseCommand
from core.tasks.task_manager import task_manager


class Command(BaseCommand):
    """
    Django management command to check task manager status.

    Usage:
        python manage.py task_status                    # Show all tasks
        python manage.py task_status --status running  # Show only running tasks
        python manage.py task_status --status pending  # Show only pending tasks
        python manage.py task_status --status completed # Show only completed tasks
        python manage.py task_status --status failed    # Show only failed tasks
    """

    help = "Check task manager status and list tasks"

    def add_arguments(self, parser):
        parser.add_argument(
            "--status",
            type=str,
            choices=["pending", "running", "completed", "failed", "cancelled"],
            help="Filter tasks by status",
        )
        parser.add_argument(
            "--clear-completed",
            action="store_true",
            help="Clear all completed and failed tasks",
        )
        parser.add_argument(
            "--cancel",
            type=str,
            help="Cancel a specific task by name",
        )

    def handle(self, *args, **options):
        status_filter = options.get("status")
        clear_completed = options.get("clear_completed")
        cancel_task = options.get("cancel")

        if clear_completed:
            cleared_count = task_manager.clear_completed_tasks()
            self.stdout.write(
                self.style.SUCCESS(f"Cleared {cleared_count} completed/failed tasks")
            )
            return

        if cancel_task:
            if task_manager.cancel_task(cancel_task):
                self.stdout.write(
                    self.style.SUCCESS(f'Task "{cancel_task}" cancelled successfully')
                )
            else:
                self.stdout.write(
                    self.style.ERROR(f'Failed to cancel task "{cancel_task}"')
                )
            return

        # Show task statistics
        total_tasks = task_manager.get_task_count()
        running_tasks = task_manager.get_task_count("running")
        pending_tasks = task_manager.get_task_count("pending")
        completed_tasks = task_manager.get_task_count("completed")
        failed_tasks = task_manager.get_task_count("failed")

        self.stdout.write(self.style.SUCCESS("Task Manager Status"))
        self.stdout.write("=" * 50)
        self.stdout.write(f"Total Tasks: {total_tasks}")
        self.stdout.write(f"Running: {running_tasks}")
        self.stdout.write(f"Pending: {pending_tasks}")
        self.stdout.write(f"Completed: {completed_tasks}")
        self.stdout.write(f"Failed: {failed_tasks}")
        self.stdout.write("")

        # List tasks
        tasks = task_manager.list_tasks(status_filter)

        if not tasks:
            self.stdout.write("No tasks found.")
            return

        self.stdout.write(f"Tasks ({len(tasks)}):")
        self.stdout.write("-" * 80)

        for task_name, task_info in tasks.items():
            status = task_info.get("status", "unknown")
            start_time = task_info.get("start_time", 0)
            finish_time = task_info.get("finish_time")
            progress = task_info.get("progress", 0)

            # Format time
            if start_time:
                from datetime import datetime

                start_str = datetime.fromtimestamp(start_time).strftime("%H:%M:%S")
            else:
                start_str = "N/A"

            if finish_time:
                finish_str = datetime.fromtimestamp(finish_time).strftime("%H:%M:%S")
            else:
                finish_str = "N/A"

            # Status color
            if status == "completed":
                status_color = self.style.SUCCESS
            elif status == "failed":
                status_color = self.style.ERROR
            elif status == "running":
                status_color = self.style.WARNING
            else:
                status_color = self.style.HTTP_INFO

            self.stdout.write(
                f"{status_color(task_name)} - {status_color(status.upper())} "
                f"({progress}%) - Start: {start_str} - Finish: {finish_str}"
            )

            # Show result or error if available
            if status == "completed" and task_info.get("result"):
                result = task_info["result"]
                if isinstance(result, dict) and result.get("success"):
                    self.stdout.write(f"  ✓ {result.get('message', 'Success')}")
                else:
                    self.stdout.write(f"  ✗ {result.get('error', 'Unknown error')}")
            elif status == "failed" and task_info.get("exception"):
                self.stdout.write(f"  ✗ {task_info['exception']}")

        self.stdout.write("")
        self.stdout.write("Use --help for more options")
