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
