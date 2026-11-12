"""Subprocess fixture for testing a hard exit against a real SQLite database."""

import json
import os
import sys
from datetime import timedelta
from unittest.mock import patch

import django
from django.conf import settings


def main():
    database, operation, field = sys.argv[1:]
    settings.DATABASES = {
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": database}
    }
    django.setup()
    from django.core.management import call_command
    from django.utils import timezone

    from core.models import Entry, Feed
    from core.models.agent import OpenAIAgent
    from core.tasks.summarize_feeds import handle_feeds_summary
    from core.tasks.translate_feeds import handle_feeds_translation

    if operation == "setup":
        call_command("migrate", verbosity=0)
        agent = OpenAIAgent.objects.create(
            name="Checkpoint", max_tokens=4096, api_key="unused"
        )
        feed = Feed.objects.create(
            name="Checkpoint",
            feed_url="https://example.com/feed",
            translator=agent,
            summarizer=agent,
            translate_title=True,
            translate_content=True,
        )
        for n in range(2):
            Entry.objects.create(
                feed=feed,
                original_title=f"Article {n}",
                original_content=f"Content {n}",
                link=f"https://example.com/{n}",
                pubdate=timezone.now() - timedelta(minutes=n),
            )
        return

    feed = Feed.objects.get(name="Checkpoint")
    if operation == "inspect":
        print(
            json.dumps(
                {
                    "values": list(
                        feed.entries.order_by("-pubdate").values_list(field, flat=True)
                    ),
                    "tokens": feed.total_tokens,
                    "characters": feed.total_characters,
                }
            )
        )
        return

    calls = 0

    def complete(**kwargs):
        nonlocal calls
        calls += 1
        with open(database + ".calls", "a") as journal:
            journal.write(json.dumps(kwargs["text"]) + "\n")
        if operation == "crash" and calls == 2:
            os._exit(41)  # No finally blocks or graceful shutdown can save progress.
        return {"text": "Completed: " + kwargs["text"], "tokens": 7, "characters": 11}

    with (
        patch.object(OpenAIAgent, "summarize", side_effect=complete),
        patch.object(OpenAIAgent, "translate", side_effect=complete),
    ):
        if field == "ai_summary":
            handle_feeds_summary([feed])
        else:
            handle_feeds_translation(
                [feed], target_field=field.removeprefix("translated_")
            )


if __name__ == "__main__":
    main()
