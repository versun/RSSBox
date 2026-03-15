---
title: External API
summary: Enablement, authentication, and MVP scope for the RSSBox integration API
---

# External API

RSSBox includes a small management API for third-party integrations. It is disabled by default and must be enabled explicitly.

## Enable The API

Set these environment variables:

```bash
EXTERNAL_API_ENABLED=1
EXTERNAL_API_TOKEN=replace-with-a-long-random-token
```

When enabled, the API base path is `/api/v1/`.

If `EXTERNAL_API_ENABLED` is unset or not `1`, all API endpoints return `404`.

If the API is enabled but `EXTERNAL_API_TOKEN` is missing, all API endpoints return `503`.

## Authentication

Use a Bearer token:

```http
Authorization: Bearer <your-token>
```

Missing or invalid tokens return `401`.

## MVP Endpoints

- `GET /api/v1/feeds`
- `GET /api/v1/feeds/{id}`
- `POST /api/v1/feeds`
- `PATCH /api/v1/feeds/{id}`
- `DELETE /api/v1/feeds/{id}`
- `POST /api/v1/feeds/{id}/refresh`
- `GET /api/v1/tags`
- `POST /api/v1/tags`
- `PATCH /api/v1/tags/{id}`
- `DELETE /api/v1/tags/{id}`
- `POST /api/v1/feeds/{id}/tags`

## Exposed Feed Fields

The API only exposes minimal safe fields:

- `id`
- `name`
- `feed_url`
- `slug`
- `target_language`
- `update_frequency`
- `max_posts`
- `fetch_article`
- `translate_title`
- `translate_content`
- `summary`
- `translation_display`
- `fetch_status`
- `translation_status`
- `last_fetch`
- `last_translate`
- `tags`

Internal logs, etags, token counters, agent configuration, and other internal-only fields are not exposed.

## Curl Examples

List feeds:

```bash
curl -H "Authorization: Bearer $EXTERNAL_API_TOKEN" \
  http://localhost:8000/api/v1/feeds
```

Create a feed:

```bash
curl -X POST \
  -H "Authorization: Bearer $EXTERNAL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "feed_url": "https://example.com/rss.xml",
    "name": "Example Feed",
    "update_frequency": 30,
    "translate_title": true
  }' \
  http://localhost:8000/api/v1/feeds
```

Patch a feed:

```bash
curl -X PATCH \
  -H "Authorization: Bearer $EXTERNAL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Renamed Feed", "summary": true}' \
  http://localhost:8000/api/v1/feeds/1
```

Queue a refresh:

```bash
curl -X POST \
  -H "Authorization: Bearer $EXTERNAL_API_TOKEN" \
  http://localhost:8000/api/v1/feeds/1/refresh
```

Replace a feed's tag set:

```bash
curl -X POST \
  -H "Authorization: Bearer $EXTERNAL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tag_ids": [1, 2]}' \
  http://localhost:8000/api/v1/feeds/1/tags
```

Create a tag:

```bash
curl -X POST \
  -H "Authorization: Bearer $EXTERNAL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Tech"}' \
  http://localhost:8000/api/v1/tags
```

## Local Verification Notes

- The refresh endpoint reuses the existing RSSBox update pipeline.
- In `DEBUG=0` or other production-like settings, Django cache is configured to use Redis via `REDIS_URL`.
- After the main refresh work succeeds, RSSBox tries to warm feed and tag caches. If Redis is unavailable, you will see log messages such as `Failed to cache RSS ...` or `Failed to cache tag ...`.
- Those cache errors do not roll back the core fetch, translation, summary, or database updates. The main refresh work finishes first.
- What remains incomplete is cache warm-up. If Redis stays unavailable, cache-backed RSS/tag output endpoints may fail until Redis is reachable again.
- For local API verification, either use `uv run dev` / `DEBUG=1` so Django falls back to its local in-process cache, or run Redis and set `REDIS_URL` explicitly.

## MVP Limitations

- Feed `create` and `patch` only save configuration. They do not fetch, translate, or summarize content automatically.
- The `refresh` endpoint only queues asynchronous work. A `202` response means accepted, not completed.
- There is no pagination, filter management API, agent management API, digest API, or task status API in this MVP.
- AI weekly and digest APIs are explicitly out of scope.

## Explicitly Out Of Scope

- AI weekly or digest API
- Unrelated refactors
- Frontend or admin redesign
- Large architecture changes
