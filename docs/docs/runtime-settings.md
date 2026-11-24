# 全局 AI 设置与文章任务

管理后台新增「全局 AI 设置」（Global AI settings），以数据库保存可选覆盖值。首次使用点击新增；以后修改同一条配置。保存后无需重启或重建 Docker；不把数据库路径、密钥等部署配置搬进页面。

## 超时优先级

1. 翻译器 `Request Timeout (s)` 显式设置。
2. 全局 AI 设置的默认请求超时。
3. Docker 环境变量 `OPENAI_API_TIMEOUT`（默认 120 秒）。

字段留空代表继承，不把环境变量默认值复制进数据库。升级时没有配置行也保持原有行为。页面显示当前生效值。

新设置用于后续发起的 HTTP 请求；已经创建的客户端/已经提交上游的请求不会被中断。这个超时不是整篇文章的总耗时上限：长文可能有多块，每块分别请求。

## 跨轮失败预算与退避

- 最大失败轮次：包含首次失败，默认继承 `ENTRY_MAX_RETRY_ATTEMPTS=3`，不是“首次 + 再重试三次”。
- 首次等待间隔：继承 `ENTRY_RETRY_BASE_DELAY_SECONDS=3600`。
- 等待上限：继承 `ENTRY_RETRY_MAX_DELAY_SECONDS=86400`。
- 第 n 轮失败后的最早重试时间为 `失败记录时间 + min(上限, 基础间隔 × 2^(n-1))`。
- 达到最大失败轮次时暂停；输出截断经拆块仍无法恢复时立即暂停，不必耗尽次数。
- 失败轮次不同于模型请求次数：正文可能有多个分块；部分瞬时错误仍有轮内重试。

示例：默认失败预算三轮，第一轮失败后至少等一小时，第二轮失败后至少等两小时，第三轮失败后自动暂停。

“最早重试时间”不是专属定时器。实际执行还要等订阅下一轮扫描、锁可用、条目仍在自动处理窗口等条件。修改全局设置只影响后续失败排期，不修改已保存的 `next_retry_at`；要立即尝试某条，用「立即补译」。超时后上游仍可能继续生成及计费，服务商额度限制仍有必要。

## 首次失败记录与默认列表

首次失败就持久化状态，不能推迟记录，否则无法累计次数、执行退避或追踪未知用量。

「失败几轮后显示在待关注列表」只控制列表筛选，不改变实际执行。默认待关注显示：达到显示阈值的失败任务、待处理任务、已暂停任务。暂停始终显示，即使未达到显示阈值。选择「全部任务」或「已成功」可查看历史成功记录；常规 Status 筛选也可单独使用。

未失败过且直接成功的文章不会自动建立任务行，因此这个列表不是所有文章的翻译质量检查台。

## 操作语义

- **暂停**：停止选中阶段后续自动尝试，不撤销已提交给供应商的请求。
- **恢复**：重置失败预算和退避，等下一轮处理；超出最新文章窗口的旧文可能仍不会被选中。
- **立即补译**：后台提交选定阶段，不清除已有译文；有结果的阶段直接同步为成功，不再发起 AI 调用。
- 已有译文仍有质量问题时，上述动作不是“强制重译”。强制重译需另行设计确认、保留旧译文与缓存失效，不能把补译按钮静默改成销毁已有结果。
- 同源锁忙的立即补译目前仍可能跳过；提交提示不是成功证明。本补丁未引入持久任务队列。
- 不提供直接删除状态动作，避免误删失败预算后重新放开自动调用；不影响文章/订阅的原有清理流程。

程序在读取任务状态、恢复预算和立即补译时对账已有结果，修复“有译文但永远 pending”。对账保留真实最后尝试时间及历史超时用量估计，不伪造一次新的模型调用。

## 部署与回退

首次安装此功能仍需升级代码并运行 `python manage.py migrate`。后续调整上述运行参数不再需要重建容器。没有修改生产数据库的数据迁移，也不在 GET 页面自动创建配置。回退代码前可在后台清空覆盖字段，继续使用环境变量；回退代码后额外的配置表可暂时保留。

## English summary

The optional singleton RuntimeConfiguration stores global request timeout and per-entry failure/backoff overrides. Agent timeout > database global override > environment default. Values are read at operation boundaries without process-local caching; no row is written during reads. Updates affect new requests and future failure scheduling, not running provider calls or existing next_retry_at values. All failures remain persisted immediately; a configurable attention-view threshold only controls visibility. Existing completed results are reconciled without additional AI requests. This change does not implement a durable queue, forced retranslation, monetary budgets, or semantic translation validation.
