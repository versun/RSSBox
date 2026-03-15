---
title: 外部 API
summary: RSSBox 第三方集成管理 API 的启用方式、认证方式和 MVP 范围
---

# 外部 API

RSSBox 提供了一个面向第三方集成的小型管理 API。该 API 默认关闭，需要显式启用。

## 启用 API

设置以下环境变量：

```bash
EXTERNAL_API_ENABLED=1
EXTERNAL_API_TOKEN=replace-with-a-long-random-token
```

启用后，API 基础路径为 `/api/v1/`。

如果 `EXTERNAL_API_ENABLED` 未设置或不等于 `1`，所有 API 端点都会返回 `404`。

如果 API 已启用但未设置 `EXTERNAL_API_TOKEN`，所有 API 端点都会返回 `503`。

## 认证

使用 Bearer Token：

```http
Authorization: Bearer <your-token>
```

缺少 token 或 token 无效时返回 `401`。

## MVP 端点

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

## Feed 暴露字段

API 仅暴露最小且安全的字段：

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

不会暴露内部日志、etag、token 统计、Agent 配置或其他仅供内部使用的字段。

## Curl 示例

列出 feeds：

```bash
curl -H "Authorization: Bearer $EXTERNAL_API_TOKEN" \
  http://localhost:8000/api/v1/feeds
```

创建 feed：

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

更新 feed：

```bash
curl -X PATCH \
  -H "Authorization: Bearer $EXTERNAL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Renamed Feed", "summary": true}' \
  http://localhost:8000/api/v1/feeds/1
```

触发刷新排队：

```bash
curl -X POST \
  -H "Authorization: Bearer $EXTERNAL_API_TOKEN" \
  http://localhost:8000/api/v1/feeds/1/refresh
```

替换 feed 的 tag 集合：

```bash
curl -X POST \
  -H "Authorization: Bearer $EXTERNAL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tag_ids": [1, 2]}' \
  http://localhost:8000/api/v1/feeds/1/tags
```

创建 tag：

```bash
curl -X POST \
  -H "Authorization: Bearer $EXTERNAL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Tech"}' \
  http://localhost:8000/api/v1/tags
```

## 本地验证说明

- 刷新流程复用了 RSSBox 现有的更新管线。
- 在 `DEBUG=0` 或其他接近生产环境的配置下，Django 缓存会通过 `REDIS_URL` 使用 Redis。
- 主刷新流程成功后，RSSBox 会继续尝试预热 feed 和 tag 缓存。如果 Redis 不可用，你会在日志中看到 `Failed to cache RSS ...` 或 `Failed to cache tag ...` 之类的报错。
- 这些缓存错误不会回滚核心的抓取、翻译、摘要或数据库更新。刷新任务的主要工作会先完成。
- 真正未完成的是缓存预热。如果 Redis 持续不可用，依赖缓存的 RSS/tag 输出端点可能会失败，直到 Redis 恢复可用。
- 本地验证 API 时，要么使用 `uv run dev` / `DEBUG=1` 让 Django 使用本地进程内缓存，要么启动 Redis 并显式设置 `REDIS_URL`。

## MVP 限制

- Feed 的 `create` 和 `patch` 只会保存配置，不会自动抓取、翻译或生成摘要。
- `refresh` 端点只负责将异步任务加入队列。返回 `202` 仅表示已接受，不表示刷新已完成。
- 这个 MVP 不包含分页、过滤器管理 API、Agent 管理 API、Digest API 或任务状态 API。
- AI 周报和摘要 API 明确不在当前范围内。

## 明确不包含

- AI weekly 或 digest API
- 无关重构
- 前端或管理后台重设计
- 大规模架构调整
