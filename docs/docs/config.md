---
title: 环境变量配置
summary: RSSBox 所有可用的环境变量配置说明
---

# 环境变量配置

本文档列出了 RSSBox 所有可用的环境变量配置选项。

## 基础配置

### SITE_URL
- **说明**: 网站的完整URL地址
- **默认值**: `http://localhost:8000`
- **示例**: `https://rssbox.example.com`

### SECRET_KEY
- **说明**: Django 密钥，用于加密会话和安全功能
- **默认值**: 自动生成随机密钥
- **示例**: `your-secret-key-here-keep-it-safe`
- **注意**: ⚠️ 生产环境必须设置固定值以保持会话一致性

### DEBUG
- **说明**: 是否启用调试模式
- **默认值**: `0` (关闭)
- **可选值**: `0` (关闭) 或 `1` (开启)
- **注意**: ⚠️ 生产环境必须设置为 `0`

## 用户与访问控制

### USER_MANAGEMENT
- **说明**: 是否启用用户管理系统
- **默认值**: `0` (关闭)
- **可选值**: `0` (关闭) 或 `1` (开启)

### DEMO
- **说明**: 是否为演示模式（限制某些功能）
- **默认值**: `0` (关闭)
- **可选值**: `0` (关闭) 或 `1` (开启)

## 安全配置

### CSRF_TRUSTED_ORIGINS
- **说明**: CSRF 信任的源地址列表，多个地址用逗号分隔
- **默认值**: `http://*`
- **示例**: `https://rssbox.example.com,https://www.example.com`

### X_FRAME_OPTIONS
- **说明**: X-Frame-Options 响应头设置
- **默认值**: `DENY`
- **可选值**: `DENY`, `SAMEORIGIN`

## 国际化与本地化

### TIME_ZONE
- **说明**: 应用程序时区设置
- **默认值**: `UTC`
- **示例**: 
  - `Asia/Shanghai` - 中国标准时间
  - `America/New_York` - 美国东部时间
  - `Europe/London` - 英国时间
- **注意**: 时区列表参考 [IANA 时区数据库](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)

### DEFAULT_TARGET_LANGUAGE
- **说明**: 默认翻译目标语言
- **默认值**: `Chinese Simplified`
- **可选值**: 
  - `English`
  - `Chinese Simplified` (简体中文)
  - `Chinese Traditional` (繁体中文)
  - `Russian`, `Japanese`, `Korean`
  - `Czech`, `Danish`, `German`
  - `Spanish`, `French`, `Indonesian`
  - `Italian`, `Hungarian`, `Norwegian Bokmal`
  - `Dutch`, `Polish`, `Portuguese`
  - `Swedish`, `Turkish`

## 日志配置

### LOG_LEVEL
- **说明**: 日志记录级别
- **默认值**: `ERROR`
- **可选值**: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
- **说明**: 
  - `DEBUG` - 最详细，包含所有调试信息
  - `INFO` - 一般信息
  - `WARNING` - 警告信息
  - `ERROR` - 错误信息（推荐用于生产环境）
  - `CRITICAL` - 严重错误

## 缓存配置

### REDIS_URL
- **说明**: Redis 服务器连接地址（仅在生产环境使用）
- **默认值**: `redis://localhost:6379/1`
- **示例**: `redis://username:password@redis-host:6379/1`
- **注意**: 在 DEBUG=0 时启用

## 使用示例

### Docker Compose 配置示例

```yaml
environment:
  - SITE_URL=https://rssbox.example.com
  - SECRET_KEY=your-secret-key-here
  - DEBUG=0
  - TIME_ZONE=Asia/Shanghai
  - DEFAULT_TARGET_LANGUAGE=Chinese Simplified
  - LOG_LEVEL=ERROR
  - CSRF_TRUSTED_ORIGINS=https://rssbox.example.com
  - REDIS_URL=redis://redis:6379/1
```

## 最佳实践

1. **生产环境必备配置**:
   - 设置固定的 `SECRET_KEY`
   - 将 `DEBUG` 设置为 `0`
   - 配置正确的 `SITE_URL`
   - 设置适当的 `CSRF_TRUSTED_ORIGINS`

2. **安全建议**:
   - 不要在代码仓库中提交包含敏感信息的 `.env` 文件
   - 使用强随机字符串作为 `SECRET_KEY`
   - 生产环境建议使用 `ERROR` 或更高的 `LOG_LEVEL`

3. **性能优化**:
   - 生产环境配置 Redis 缓存以提升性能
   - 根据服务器位置设置合适的 `TIME_ZONE`
