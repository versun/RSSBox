## How to backup data

All metadata is in the `data/db.sqlite3` database, you can backup that file yourself.

## Error: CSRF Authentication Failure

If you get a 403 CSRF authentication failure error after logging in, you need to set the environment variable CSRF_TRUSTED_ORIGINS to the domain name or IP address:https://example.com:port,http://example.com:port

## Server 500 reporting errors

If it is deployed on Railway, wait for 5 minutes and try again.

If you deployed on other way, wait for 5 minutes and still can't solve the problem, then restart the instance or service and try again.

## Fetch Failed

1. check if your source address is correct and accessible
2. Check if your server has normal network access.
3. If you are deploying locally, check your proxy settings, preferably global proxy.

## Translation Failed

1. Check if the translation engine is working
2. Check if your server network has access to the translation engine's server.

## Why is some content not translated

1. check if you have set a maximum number of entries, this value limits the number of translations
2. If you use free translation engines, such as Google Translate and DeepLX, the original content will be displayed as it is easy to fail to translate due to the rate limitation. It is recommended to use paid translation engines for translation

## My Reading can't subscribe to the translated address

1. Check the translation status in the RSS translator to see if it is complete.
2. Use your browser to access the address if it works
3. your reader may not be able to access the RSS Translator, check if your RSS Translator is set to open to the public.

### IPv6

It is currently not possible to support both IPv4 and IPv6;

If you want to listen to IPv6 addresses instead, just modify the deploy/start.sh file, change `0.0.0.0` to `::`, and restart the service.

### Can I set up a proxy server?

The RSS translator itself doesn't support setting a global proxy, but you can add the following 2 environment variables to set a global proxy: 
```HTTP_PROXY=http://proxy.example.com:8080
HTTPS_PROXY=http://proxy.example.com:8080

```

### Still can't get it to work?
Please [Submit an Issue](https://github.com/versun/RSS-Translator/issues) or give feedback in [Telegram Group](https://t.me/rsstranslator)!
```