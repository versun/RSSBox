---
title: Architecture
summary: Current RSSBox code structure and responsibilities
---

# Architecture

RSSBox is currently organized into these main layers:

## `core/models`

Stores data structures and minimal model behavior.

- `feed.py`: RSS source configuration, status, and usage stats
- `entry.py`: article content
- `filter.py`: filter configuration and filter result cache
- `agent.py`: configuration models for translation and summary agents
- `tag.py`: tags and aggregated feed configuration

## `core/services`

Contains the actual application logic.

### `core/services/feed`

All Feed-related workflow logic lives here:

- `pipeline.py`: single-feed update pipeline
- `refresh.py`: cache refresh and tag aggregation refresh
- `filters.py`: filter rules for feeds and tags
- `rendering.py`: RSS/Atom content generation
- `response.py`: RSS/JSON response wrapping

### `core/services/admin`

Admin action logic:

- `actions.py`: force update and tag recombination
- `batch.py`: batch modification

### `core/services/agent`

Concrete implementations for agent behavior:

- `openai.py`
- `deepl.py`
- `libretranslate.py`
- `test_agent.py`

### `core/services/opml.py`

OPML import and export.

## `core/views.py`

Only handles requests, cache lookup, service calls, and responses.

## `core/actions.py`

Only keeps Django admin action entry points and redirect/render behavior.

## `core/management/commands`

Command entry layer.  
The main command is `feed_updater.py`, which schedules the update workflow.

## `core/cache.py`

Cache entry layer.  
It manages cache keys and delegates content generation to services.

## Maintenance rules

Future changes should follow these rules:

1. Views and commands should stay as entry points, not hold large workflow logic.
2. Models should keep configuration and minimal behavior; complex workflows belong in `services`.
3. Rules of the same kind should live in one place only.
4. New behavior should add tests first, then service-layer implementation.
