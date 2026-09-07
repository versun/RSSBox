---
title: Refactor Notes
summary: Main structural changes from the refactor and where to maintain things now
---

# Refactor Notes

The goal of this refactor was simple: views, models, admin actions, commands, and services should each do one kind of work.

## Main changes

### 1. Digest removed

- Removed Digest code, routes, admin entry points, scheduled tasks, and related documentation
- Kept cleanup logic for old Digest-generated data

### 2. Feed workflow unified

- Single-feed update workflow moved to `core/services/feed/pipeline.py`
- Post-update refresh logic moved to `core/services/feed/refresh.py`

### 3. Output and filtering unified

- Feed rendering moved to `core/services/feed/rendering.py`
- RSS/JSON response wrapping moved to `core/services/feed/response.py`
- Feed and tag filtering rules moved to `core/services/feed/filters.py`

### 4. Admin actions unified

- Force update and tag recombination moved to `core/services/admin/actions.py`
- Batch modification moved to `core/services/admin/batch.py`

### 5. Agent execution logic extracted

- OpenAI / DeepL / LibreTranslate / TestAgent execution logic moved to `core/services/agent/`
- `core/models/agent.py` now mainly keeps fields and thin wrappers

### 6. Prompt defaults extracted

- Default prompts moved to `core/prompts.py`
- `config/settings.py` now keeps configuration plus compatibility exports

## Current maintenance entry points

If you need to change something later:

- Feed update flow: `core/services/feed/pipeline.py`
- Refresh and cache behavior: `core/services/feed/refresh.py`
- Output generation: `core/services/feed/rendering.py` and `response.py`
- Filtering rules: `core/services/feed/filters.py`
- Admin actions: `core/services/admin/`
- OPML: `core/services/opml.py`
- Agent behavior: `core/services/agent/`

## Rules to keep

1. Views and commands stay as entry points only
2. Models keep data and minimal behavior; complex logic belongs in services
3. Rules of the same kind should live in one place only
4. New behavior should add tests first, then service-layer implementation
