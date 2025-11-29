# Frequently Asked Questions

## How to backup data

All metadata is in the `data/db.sqlite3` database, you can backup that file yourself.

## Error: CSRF Authentication Failure

If you get a 403 CSRF authentication failure error after logging in, you need to set the environment variable CSRF_TRUSTED_ORIGINS to the domain name or IP address:https://example.com:port,http://example.com:port

## Server 500 Error

If deployed on Railway, wait for 5 minutes and try again.

If deployed using other methods, wait for 5 minutes and if the problem persists, restart the instance or service.

## Feed Fetch Failed

1. Check if your source address is correct and accessible
2. Check if your server has normal network access
3. If deploying locally, check your proxy settings, preferably use global proxy

## Translation Status Error

1. Check if the translation engine is valid
2. Check if your server network can access the translation engine's server

## Why is some content not translated

1. Check if you have set a maximum number of entries, this value limits the number of translations
2. If using free translation engines like Google Translate and DeepLX, due to rate limitations, translation easily fails, so original content will be displayed. It is recommended to use paid translation engines for translation

## AI Feature Related Issues

### AI Filter Not Working
1. Check if AI engine is correctly configured and valid
2. Check if filter prompts are clear and specific

### AI Digest Generation Failed
1. Check if RSS sources are correctly created and associated with tags
2. Confirm that AI engine used for digest generation is working properly
3. Check if tags have sufficient content (recommend 3-5 active sources)

### Tag System Issues
1. Check if RSS sources are correctly associated with tags
2. Confirm that associated sources have new content updates
3. Check if tag filter configuration is correct

## My RSS reader cannot subscribe to the translated address

1. In RSSBox, check if translation status is complete
2. Use browser to access the address to see if it works normally
3. Your reader may not be able to access RSSBox, check if your RSSBox is set to open to the public

### IPv6

Currently cannot support both IPv4 and IPv6 simultaneously;

If you want to listen to IPv6 addresses instead, just modify the deploy/start.sh file, change `0.0.0.0` to `::`, and restart the service.

### Can I set up a proxy server?

RSSBox itself does not support setting a global proxy, but you can add the following 2 environment variables to set a global proxy:
```
HTTP_PROXY=http://proxy.example.com:8080
HTTPS_PROXY=http://proxy.example.com:8080
```

### How to configure timezone

You can configure the system timezone by setting the `TIME_ZONE` environment variable, for example:
```
TIME_ZONE=Asia/Shanghai      # Beijing Time
TIME_ZONE=America/New_York   # New York Time
TIME_ZONE=Europe/London      # London Time
```
If this environment variable is not set, the system defaults to UTC timezone.

### Cloudflare SSL

If Cloudflare's DNS proxy is enabled, you need to select Full for encryption mode on Cloudflare's SSL/TLS page.

### Still can't resolve the issue?
Please [Submit an Issue](https://github.com/versun/rssbox/issues) or provide feedback in the [Telegram Group](https://t.me/rssboxapp)