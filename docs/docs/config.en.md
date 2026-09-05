---
title: Environment Variables Configuration
summary: Complete guide to all available environment variables in RSSBox
---

# Environment Variables Configuration

This document lists all available environment variable configuration options for RSSBox.

## Basic Configuration

### SITE_URL
- **Description**: Full URL address of the website
- **Default**: `http://localhost:8000`
- **Example**: `https://rssbox.example.com`

### SECRET_KEY
- **Description**: Django secret key for encryption and security features
- **Default**: Auto-generated random key
- **Example**: `your-secret-key-here-keep-it-safe`
- **Note**: ⚠️ Must set a fixed value in production to maintain session consistency

### DEBUG
- **Description**: Enable debug mode
- **Default**: `0` (disabled)
- **Options**: `0` (disabled) or `1` (enabled)
- **Note**: ⚠️ Must be set to `0` in production

## User & Access Control

### USER_MANAGEMENT
- **Description**: Enable user management system
- **Default**: `0` (disabled)
- **Options**: `0` (disabled) or `1` (enabled)

### DEMO
- **Description**: Demo mode (restricts certain features)
- **Default**: `0` (disabled)
- **Options**: `0` (disabled) or `1` (enabled)

## Security Configuration

### CSRF_TRUSTED_ORIGINS
- **Description**: List of CSRF trusted origins, separated by commas
- **Default**: `http://*`
- **Example**: `https://rssbox.example.com,https://www.example.com`

### X_FRAME_OPTIONS
- **Description**: X-Frame-Options response header setting
- **Default**: `DENY`
- **Options**: `DENY`, `SAMEORIGIN`

## Internationalization & Localization

### TIME_ZONE
- **Description**: Application timezone setting
- **Default**: `UTC`
- **Examples**: 
  - `Asia/Shanghai` - China Standard Time
  - `America/New_York` - Eastern Time
  - `Europe/London` - UK Time
- **Note**: See [IANA time zone database](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) for full list

### DEFAULT_TARGET_LANGUAGE
- **Description**: Default translation target language
- **Default**: `Chinese Simplified`
- **Options**: 
  - `English`
  - `Chinese Simplified`
  - `Chinese Traditional`
  - `Russian`, `Japanese`, `Korean`
  - `Czech`, `Danish`, `German`
  - `Spanish`, `French`, `Indonesian`
  - `Italian`, `Hungarian`, `Norwegian Bokmal`
  - `Dutch`, `Polish`, `Portuguese`
  - `Swedish`, `Turkish`

## Logging Configuration

### LOG_LEVEL
- **Description**: Logging level
- **Default**: `ERROR`
- **Options**: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
- **Details**: 
  - `DEBUG` - Most verbose, includes all debug information
  - `INFO` - General information
  - `WARNING` - Warning messages
  - `ERROR` - Error messages (recommended for production)
  - `CRITICAL` - Critical errors

## OpenAI API Configuration

### OPENAI_API_TIMEOUT
- **Description**: Timeout in seconds for a single OpenAI-compatible API request
- **Default**: `120`
- **Example**: `300`
- **Details**:
  - Increase this for long article translations
  - Retry behavior after timeout is controlled by `OPENAI_API_MAX_RETRIES`

### OPENAI_API_MAX_RETRIES
- **Description**: SDK-level automatic retry count for OpenAI-compatible API requests
- **Default**: `0`
- **Example**: `0`
- **Details**:
  - Disabled by default to avoid resending the same request after a timeout
  - Increase it only if you explicitly want automatic retries

## Fetch Configuration

### RSS_FETCH_USER_AGENT
- **Description**: Custom User-Agent used when fetching feeds
- **Default**: empty (uses a random browser UA; automatically retries with a fallback UA when blocked by a WAF)
- **Example**: `curl/8.10.1`
- **Note**: Some sites (e.g. behind Cloudflare WAF) block browser UAs; setting a simple UA can bypass this

## Cache Configuration

### REDIS_URL
- **Description**: Redis server connection URL (production only)
- **Default**: `redis://localhost:6379/1`
- **Example**: `redis://username:password@redis-host:6379/1`
- **Note**: Enabled when DEBUG=0

## Usage Examples

### Docker Compose Configuration

```yaml
environment:
  - SITE_URL=https://rssbox.example.com
  - SECRET_KEY=your-secret-key-here
  - DEBUG=0
  - TIME_ZONE=Asia/Shanghai
  - DEFAULT_TARGET_LANGUAGE=Chinese Simplified
  - OPENAI_API_TIMEOUT=300
  - OPENAI_API_MAX_RETRIES=0
  - LOG_LEVEL=ERROR
  - CSRF_TRUSTED_ORIGINS=https://rssbox.example.com
  - REDIS_URL=redis://redis:6379/1
```

## Best Practices

1. **Production Essentials**:
   - Set a fixed `SECRET_KEY`
   - Set `DEBUG` to `0`
   - Configure correct `SITE_URL`
   - Set appropriate `CSRF_TRUSTED_ORIGINS`

2. **Security Recommendations**:
   - Never commit `.env` files with sensitive information to version control
   - Use strong random strings for `SECRET_KEY`
   - Use `ERROR` or higher `LOG_LEVEL` in production

3. **Performance Optimization**:
   - Configure Redis cache in production for better performance
   - Set appropriate `TIME_ZONE` based on server location
