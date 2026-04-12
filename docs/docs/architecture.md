---
title: 代码结构
summary: 当前 RSSBox 的主要代码分层与职责
---

# 代码结构

RSSBox 当前的代码主要分成这几层：

## `core/models`

保存数据结构和最基本的模型行为。

- `feed.py`：RSS 源配置、状态和统计信息
- `entry.py`：文章内容
- `filter.py`：过滤器配置和过滤结果缓存
- `agent.py`：各类翻译/摘要 Agent 的配置模型
- `tag.py`：标签与聚合源配置

## `core/services`

保存真正的业务处理逻辑。

### `core/services/feed`

和 Feed 主流程相关的处理都在这里：

- `pipeline.py`：单个 Feed 的更新流程
- `refresh.py`：更新后的缓存刷新与标签聚合刷新
- `filters.py`：Feed 与 Tag 的过滤规则
- `rendering.py`：RSS/Atom 输出内容生成
- `response.py`：RSS/JSON 返回格式包装

### `core/services/admin`

后台动作相关的处理：

- `actions.py`：强制更新、标签重组
- `batch.py`：批量修改

### `core/services/agent`

不同 Agent 的具体执行逻辑：

- `openai.py`
- `deepl.py`
- `libretranslate.py`
- `test_agent.py`

### `core/services/opml.py`

OPML 导入与导出。

## `core/views.py`

只负责接收请求、读取缓存、调用服务并返回响应。

## `core/actions.py`

只负责 Django admin action 入口与页面跳转，不再保存复杂业务逻辑。

## `core/management/commands`

命令入口层。  
当前重点命令是 `feed_updater.py`，负责按频率调度更新流程。

## `core/cache.py`

缓存入口层。  
负责读写缓存键，并调用服务层生成输出内容。

## 维护原则

后续改动优先遵守这几点：

1. 页面和命令只做入口，不直接写大段业务逻辑。
2. 模型保留配置和最小行为，复杂流程放进 `services`。
3. 同一类规则只放一个位置，避免“改一处漏两处”。
4. 新功能优先先补测试，再进入服务层实现。
