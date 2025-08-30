# RSS-Translator 日报功能使用指南

## 功能概述

RSS-Translator 的日报功能是一个智能内容聚合系统，能够：

1. **智能聚类**: 使用AI技术将相关文章按主题自动分组
2. **综合分析**: 为每个主题生成200-300字的深度分析文章
3. **RSS订阅**: 提供标准的RSS和JSON格式订阅链接
4. **灵活配置**: 通过Django管理界面进行所有配置

## 安装和配置

### 1. 安装依赖

```bash
# 安装新的Python依赖
uv add scikit-learn jieba numpy

# 运行数据库迁移
uv run python manage.py migrate
```

### 2. 创建日报配置

1. 访问管理界面: `/admin/`
2. 进入 "Digests" 部分
3. 点击 "添加 Digest"
4. 配置以下信息：

#### 基本信息
- **名称**: 日报的显示名称
- **描述**: 日报的简要说明
- **是否激活**: 开启自动生成

#### 生成配置
- **标签**: 选择要聚合的RSS标签（数据源）
- **每日文章数**: 目标生成的文章数量（建议3-8篇）
- **生成时间**: 每日自动生成的小时数（0-23）
- **最小聚类大小**: 每个聚类至少包含的文章数（建议3篇）

#### AI配置
- **AI代理**: 选择用于聚类和文章生成的OpenAI代理
- **系统提示词**: AI的角色设定（可选）
- **文章生成提示词**: 文章生成的具体指导（可选）

### 3. 配置AI代理

确保你已经配置了OpenAI代理：
1. 进入 "Agents" -> "添加 OpenAI"
2. 配置API密钥和模型
3. 测试代理可用性

## 使用方法

### 手动生成日报

```bash
# 生成特定日报
uv run python manage.py generate_digest --digest-id 1

# 强制重新生成（即使今天已生成）
uv run python manage.py generate_digest --digest-id 1 --force

# 生成所有当前小时配置的日报
uv run python manage.py generate_digest --all

# 查看统计信息
uv run python manage.py generate_digest --stats

# 清理30天前的旧记录
uv run python manage.py generate_digest --cleanup 30
```

### 自动生成设置

日报会根据配置的"生成时间"自动生成。你可以设置cron任务：

```bash
# 添加到crontab，每小时检查一次
0 * * * * cd /path/to/RSS-Translator && uv run python manage.py generate_digest --all
```

### 订阅链接

生成的日报可以通过以下格式订阅：

```
# RSS格式
https://your-domain.com/core/digest/rss/{digest_slug}

# JSON格式  
https://your-domain.com/core/digest/json/{digest_slug}

# 日报列表
https://your-domain.com/core/digest/

# 文章详情
https://your-domain.com/core/digest/article/{article_id}

# 状态查询
https://your-domain.com/core/digest/status/{digest_slug}
```

## 工作流程

1. **数据收集**: 从配置的标签获取最近24小时的文章
2. **AI聚类**: 使用OpenAI进行语义聚类分析
3. **文章生成**: 为每个聚类生成综合分析文章
4. **质量评估**: 自动评估文章质量并决定是否发布
5. **RSS生成**: 生成可订阅的RSS/JSON格式

## 特性说明

### 智能聚类
- 优先使用AI语义聚类（更准确）
- 备用传统机器学习聚类
- 自动提取关键词和主题

### 内容生成
- 包含时间线、核心观点、深度分析、影响评估
- 自动计算阅读时间
- 保留原文链接

### 质量控制
- 基于多维度的质量评分系统
- 高质量文章自动发布
- 低质量文章保存为草稿

## 监控和管理

### 管理界面功能
- 查看生成历史和统计
- 手动生成和重新生成
- 批量操作（激活/停用/发布）
- 错误日志查看

### 关键指标
- 生成成功率
- 文章发布率
- Token消耗统计
- 平均质量评分

## 故障排除

### 常见问题

1. **生成失败**
   - 检查AI代理配置
   - 确认标签包含足够的文章
   - 查看错误日志

2. **聚类效果不佳**
   - 调整最小聚类大小
   - 检查文章内容质量
   - 考虑调整时间范围

3. **文章质量不高**
   - 优化提示词模板
   - 选择更好的AI模型
   - 调整聚类参数

### 调试命令

```bash
# 查看详细日志
uv run python manage.py generate_digest --digest-id 1 --verbosity 2

# 测试特定时间段
uv run python manage.py generate_digest --hour 8 --all
```

## 高级配置

### 自定义提示词模板

可以在日报配置中自定义AI提示词：

```
你是一位资深的{领域}分析师，需要根据相关新闻条目生成一篇深度分析文章。

文章要求：
1. 标题：简洁有力，体现核心主题
2. 结构清晰：包含{模块列表}
3. 长度：{目标长度}字
4. 语言：{target_language}
5. 风格：{写作风格}
```

### 内容模块配置

可以在日报配置中自定义内容结构：

```json
[
  {"name": "timeline", "title": "时间线", "weight": 0.2},
  {"name": "summary", "title": "核心观点", "weight": 0.3},
  {"name": "analysis", "title": "深度分析", "weight": 0.3},
  {"name": "impact", "title": "影响评估", "weight": 0.2}
]
```

## 最佳实践

1. **标签配置**: 选择相关性高的标签，避免过于分散
2. **生成时间**: 选择RSS源更新较少的时间段
3. **文章数量**: 根据内容量调整，避免过多或过少
4. **AI模型**: 使用较新的模型获得更好效果
5. **监控**: 定期查看生成质量和统计信息

通过合理配置和使用，RSS-Translator的日报功能能够帮助你将信息阅读量减少90%，同时保持对重要内容的全面掌握。