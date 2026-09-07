def libretranslate_api_request(
    agent,
    endpoint,
    *,
    params=None,
    method="POST",
    request_module,
    parse_module,
    json_module,
    settings_module,
):
    try:
        url = agent.server_url
        if not url.endswith("/"):
            url += "/"
        full_url = f"{url}{endpoint}"

        query_params = params or {}
        if agent.api_key:
            query_params["api_key"] = agent.api_key

        data = parse_module.urlencode(query_params).encode("utf-8")
        req = request_module.Request(full_url, data=data, method=method)
        req.add_header("accept", "application/json")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("User-Agent", "LibreTranslateAgent/1.0")

        with request_module.urlopen(req, timeout=settings_module.LT_TIMEOUT) as response:
            response_str = response.read().decode("utf-8")
            return json_module.loads(response_str)
    except Exception as exc:
        raise ConnectionError(f"_api_request {str(exc)}")


def libretranslate_api_translate(agent, q, source, target, *, format="html", api_request_func):
    params = {"q": q, "source": source, "target": target, "format": format}
    response_data = api_request_func("translate", params=params, method="POST")
    if "error" in response_data:
        raise Exception(f"_api_translate Error: {response_data['error']}")
    return response_data.get("translatedText", "")


def libretranslate_api_languages(agent, *, api_request_func):
    return api_request_func("languages", method="GET")


def libretranslate_validate(agent, *, api_languages_func, timezone_module, save_func):
    is_valid = False
    try:
        api_languages_func()
        agent.log = ""
        is_valid = True
    except Exception as exc:
        agent.log = f"{timezone_module.now()}: {str(exc)}"
        is_valid = False
    finally:
        agent.valid = is_valid
        save_func()
    return is_valid


def libretranslate_translate(
    agent,
    text,
    target_language,
    *,
    api_translate_func,
    logger,
    timezone_module,
    save_func,
):
    target_code = agent.language_map.get(target_language)
    if not target_code:
        agent.log += f"{timezone_module.now()}: Not support target language: {target_language}"
        logger.error(
            f"LibreTranslateAgent->Not support target language: {target_language}"
        )
        save_func()
        return {"text": "", "characters": 0}

    try:
        translated_text = api_translate_func(
            q=text,
            source="auto",
            target=target_code,
            format="html",
        )
        return {"text": translated_text, "characters": len(text)}
    except Exception as exc:
        logger.error("LibreTranslateAgent->: %s", str(exc))
        agent.log = f"{timezone_module.now()}: {str(exc)}"
        save_func()
        return {"text": "", "characters": 0}
