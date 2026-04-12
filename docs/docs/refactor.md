---
title: 重构说明
summary: 本次结构重构后的重点变化与维护入口
---

# 重构说明

这次重构的目标很直接：让页面、模型、后台动作、命令、服务各自只负责自己的事情。

## 已完成的主要调整

### 1. 移除 Digest

- 删除了 Digest 功能的代码、路由、后台入口、定时任务和文档说明
- 清理了与 Digest 相关的旧数据迁移逻辑

### 2. 统一 Feed 主流程

- 单个 Feed 的更新流程集中到 `core/services/feed/pipeline.py`
- 批量更新后的刷新逻辑集中到 `core/services/feed/refresh.py`

### 3. 统一输出与过滤

- 输出生成集中到 `core/services/feed/rendering.py`
- RSS/JSON 响应包装集中到 `core/services/feed/response.py`
- Feed 和 Tag 的过滤规则集中到 `core/services/feed/filters.py`

### 4. 统一后台动作

- 强制更新、标签重组集中到 `core/services/admin/actions.py`
- 批量修改集中到 `core/services/admin/batch.py`

### 5. 抽离 Agent 执行逻辑

- OpenAI / DeepL / LibreTranslate / TestAgent 的执行细节集中到 `core/services/agent/`
- `core/models/agent.py` 现在只保留字段和薄包装

### 6. 抽离提示词

- 默认提示词迁到 `core/prompts.py`
- `config/settings.py` 只保留配置和对外兼容变量

## 当前维护入口

如果以后要继续改：

- 改 Feed 更新：看 `core/services/feed/pipeline.py`
- 改刷新与缓存：看 `core/services/feed/refresh.py`
- 改输出格式：看 `core/services/feed/rendering.py` 和 `response.py`
- 改过滤规则：看 `core/services/feed/filters.py`
- 改后台动作：看 `core/services/admin/`
- 改 OPML：看 `core/services/opml.py`
- 改 Agent 行为：看 `core/services/agent/`

## 当前原则

后续改动优先保持这几条：

1. 页面和命令只做入口，不直接写复杂流程
2. 模型保留数据和最少行为，复杂逻辑放服务层
3. 同一类规则只放一个位置
4. 新行为优先先写测试，再落到服务层
