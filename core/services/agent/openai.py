def openai_advanced_default():
    return {"temperature": 0.2, "reasoning_effort": "minimal"}


def openai_init(agent, *, openai_client_cls, settings_module):
    return openai_client_cls(
        api_key=agent.api_key,
        base_url=agent.base_url,
        timeout=settings_module.OPENAI_API_TIMEOUT,
        max_retries=settings_module.OPENAI_API_MAX_RETRIES,
    )


def openai_wait_for_rate_limit(
    agent,
    *,
    cache_backend,
    datetime_module,
    sleep_func,
    logger,
):
    if agent.rate_limit_rpm <= 0:
        return

    current_minute = datetime_module.datetime.now().strftime("%Y%m%d%H%M")
    cache_key = f"openai_rate_limit_{agent.id}_{current_minute}"
    request_count = cache_backend.get(cache_key, 0)

    if request_count >= agent.rate_limit_rpm:
        now = datetime_module.datetime.now()
        next_minute = now.replace(second=0, microsecond=0) + datetime_module.timedelta(
            minutes=1
        )
        wait_seconds = (next_minute - now).total_seconds() + 0.1
        logger.info(f"Rate limit reached. Waiting {wait_seconds:.2f} seconds...")
        sleep_func(wait_seconds)
        cache_backend.delete(cache_key)
        return

    cache_backend.set(cache_key, request_count + 1, timeout=60)


def openai_validate(
    agent,
    *,
    init_client,
    wait_for_rate_limit,
    task_submit,
    logger,
    settings_module,
    timezone_module,
    save_func,
):
    if not agent.api_key:
        return None

    try:
        client = init_client()
        wait_for_rate_limit()

        system_prompt = "You must only reply with exactly one character: 1"
        user_content = "1"
        if agent.merge_system_prompt:
            merged_content = f"{system_prompt}\n\n{user_content}"
            messages = [{"role": "user", "content": merged_content}]
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]

        res = client.with_options(
            max_retries=settings_module.OPENAI_API_MAX_RETRIES
        ).chat.completions.create(
            extra_headers=agent.EXTRA_HEADERS,
            model=agent.model,
            messages=messages,
            max_completion_tokens=50,
        )
        _ = res.choices[0].finish_reason
        if agent.max_tokens == 0:
            task_submit(
                f"detect_model_limit_{agent.model}_{agent.id}",
                agent.detect_model_limit,
                force=True,
            )
            logger.info(
                f"Submitted background task to detect model limit for {agent.model}"
            )
        agent.log = ""
        agent.valid = True
        return True
    except Exception as exc:
        logger.error("OpenAIAgent validate ->%s", exc)
        agent.log = f"{timezone_module.now()}: {str(exc)}"
        agent.valid = False
        return False
    finally:
        save_func(update_fields=["log", "valid"])


def openai_detect_model_limit(
    agent,
    *,
    force=False,
    init_client,
    wait_for_rate_limit,
    logger,
):
    if not force and agent.max_tokens != 0:
        return agent.max_tokens

    initial_model = agent.model
    initial_max_tokens = agent.max_tokens

    def binary_search_limit(low, high):
        if high - low <= 256:
            return low

        mid = (low + high) // 2
        try:
            wait_for_rate_limit()
            response = init_client().chat.completions.create(
                extra_headers=agent.EXTRA_HEADERS,
                model=agent.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You must only reply with exactly one character: 1",
                    },
                    {"role": "user", "content": "1"},
                ],
                max_completion_tokens=mid,
                temperature=0,
                stop=[",", "\n", " ", ".", "1"],
            )
            if response.choices[0].finish_reason == "stop":
                return binary_search_limit(mid, high)
        except Exception as exc:
            error_str = str(exc).lower()
            if any(
                keyword in error_str
                for keyword in ["maximum", "limit", "tokens", "context", "length"]
            ):
                return binary_search_limit(low, mid)
            logger.warning(f"Detect model limit when non-limit error occurs: {exc}")
            return low

    final_limit = binary_search_limit(4096, 1000000)
    agent.max_tokens = final_limit

    if agent.pk is None:
        return final_limit

    updated = type(agent).objects.filter(
        pk=agent.pk,
        model=initial_model,
        max_tokens=initial_max_tokens,
    ).update(max_tokens=final_limit)
    if updated:
        return final_limit

    current_max_tokens = (
        type(agent)
        .objects.filter(pk=agent.pk)
        .values_list("max_tokens", flat=True)
        .first()
    )
    if current_max_tokens is not None:
        agent.max_tokens = current_max_tokens
        return current_max_tokens

    return final_limit


def openai_completions(
    agent,
    text,
    *,
    system_prompt=None,
    user_prompt=None,
    _is_chunk=False,
    init_client,
    wait_for_rate_limit,
    task_submit,
    logger,
    settings_module,
    get_token_count_func,
    adaptive_chunking_func,
    save_func,
    **kwargs,
):
    client = init_client()
    tokens = 0
    result_text = ""
    log_updated = False

    try:
        if user_prompt:
            system_prompt += f"\n\n{user_prompt}"

        wait_for_rate_limit()

        if agent.merge_system_prompt:
            merged_content = f"{system_prompt}\n\n{text}"
            messages = [{"role": "user", "content": merged_content}]
            system_prompt_tokens = 0
            input_tokens = get_token_count_func(merged_content)
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ]
            system_prompt_tokens = get_token_count_func(system_prompt)
            input_tokens = get_token_count_func(system_prompt) + get_token_count_func(text)

        if agent.max_tokens == 0:
            task_submit(
                f"detect_model_limit_{agent.model}_{agent.id}",
                agent.detect_model_limit,
                force=True,
            )
            raise ValueError(
                "max_tokens is not set, Please wait for the model limit detection to complete"
            )

        if agent.merge_system_prompt:
            system_prompt_token_cost = get_token_count_func(system_prompt)
            max_usable_tokens = agent.max_tokens - system_prompt_token_cost - 100
        else:
            max_usable_tokens = agent.max_tokens - system_prompt_tokens - 100

        if get_token_count_func(text) > max_usable_tokens:
            logger.info(
                f"Text too large ({get_token_count_func(text)} tokens), chunking..."
            )
            chunks = adaptive_chunking_func(
                text,
                target_chunks=max(1, int(len(text) / max_usable_tokens)),
                min_chunk_size=500,
                max_chunk_size=max_usable_tokens,
            )
            translated_chunks = []
            for chunk in chunks:
                result = openai_completions(
                    agent,
                    chunk,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    _is_chunk=True,
                    init_client=init_client,
                    wait_for_rate_limit=wait_for_rate_limit,
                    task_submit=task_submit,
                    logger=logger,
                    settings_module=settings_module,
                    get_token_count_func=get_token_count_func,
                    adaptive_chunking_func=adaptive_chunking_func,
                    save_func=save_func,
                    **kwargs,
                )
                translated_chunks.append(result["text"])
                tokens += result["tokens"]

            return {"text": " ".join(translated_chunks), "tokens": tokens}

        output_token_limit = int(max(4096, (agent.max_tokens - input_tokens) * 0.8))
        adv_params = agent.advanced_params or {}
        if not isinstance(adv_params, dict):
            adv_params = {}

        call_kwargs = {**adv_params}
        if (
            "max_completion_tokens" not in call_kwargs
            and "max_tokens" not in call_kwargs
        ):
            call_kwargs["max_completion_tokens"] = output_token_limit

        res = client.with_options(
            max_retries=settings_module.OPENAI_API_MAX_RETRIES
        ).chat.completions.create(
            extra_headers=agent.EXTRA_HEADERS,
            model=agent.model,
            messages=messages,
            **call_kwargs,
        )
        if (
            res.choices
            and res.choices[0].finish_reason == "stop"
            and res.choices[0].message.content
        ):
            result_text = res.choices[0].message.content
            logger.debug(f"[{agent.name}]: {result_text[:50]}...")
        else:
            finish_reason = None
            if res.choices:
                try:
                    finish_reason = res.choices[0].finish_reason
                except Exception:
                    finish_reason = None
            logger.warning(
                f"[{agent.name}]: Failed to complete request:[{finish_reason or 'unknown'}]"
            )

        tokens = res.usage.total_tokens if getattr(res, "usage", None) else 0
    except Exception as exc:
        from django.utils import timezone

        agent.log = f"{timezone.now()}: {str(exc)}"
        log_updated = True
        logger.error(f"{agent.name}: {exc}")

    if not _is_chunk and log_updated:
        save_func(update_fields=["log"])

    return {"text": result_text, "tokens": tokens}


def openai_translate(
    agent,
    text,
    target_language,
    *,
    user_prompt=None,
    text_type="title",
    completions_func,
    logger,
    **kwargs,
):
    logger.info(f">>>Start Translate [{target_language}]: {text[:50]}...")
    system_prompt = (
        agent.title_translate_prompt
        if text_type == "title"
        else agent.content_translate_prompt
    ).replace("{target_language}", target_language)
    return completions_func(
        text,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        **kwargs,
    )


def openai_summarize(agent, text, target_language, *, completions_func, logger, **kwargs):
    logger.info(f">>> Start Summarize [{target_language}]: {text[:50]}...")
    system_prompt = agent.summary_prompt.replace("{target_language}", target_language)
    return completions_func(text, system_prompt=system_prompt, **kwargs)


def openai_filter(agent, text, system_prompt, *, completions_func, logger, settings_module, **kwargs):
    logger.info(f">>> Start Filter: {text[:50]}...")
    passed = False
    tokens = 0
    results = completions_func(
        text,
        system_prompt=system_prompt + settings_module.output_format_for_filter_prompt,
        **kwargs,
    )

    if results["text"] and "Passed" in results["text"]:
        logger.info(">>> Filter Passed")
        passed = True
        tokens = results["tokens"]
    else:
        logger.info(">>> Filter Blocked")
        passed = False

    return {"passed": passed, "tokens": tokens}
