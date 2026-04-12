def deepl_init(agent, *, translator_cls):
    return translator_cls(
        agent.api_key,
        server_url=agent.server_url,
        proxy=agent.proxy,
    )


def deepl_validate(agent, *, init_client, logger, timezone_module, save_func):
    is_valid = False
    try:
        translator = init_client()
        usage = translator.get_usage()
        if usage.character.valid:
            agent.log = ""
            is_valid = True
    except Exception as exc:
        logger.error("DeepLTranslator validate ->%s", exc)
        agent.log = f"{timezone_module.now()}: {str(exc)}"
        is_valid = False
    finally:
        agent.valid = is_valid
        save_func()
    return is_valid


def deepl_translate(agent, text, target_language, *, init_client, logger, timezone_module, save_func):
    logger.info(">>> DeepL Translate [%s]: %s", target_language, text)
    target_code = agent.language_code_map.get(target_language, None)
    translated_text = ""
    try:
        if target_code is None:
            logger.error(
                "DeepLTranslator->Not support target language:%s", target_language
            )
        translator = init_client()
        resp = translator.translate_text(
            text,
            target_lang=target_code,
            preserve_formatting=True,
            split_sentences="nonewlines",
            tag_handling="html",
        )
        translated_text = resp.text
    except Exception as exc:
        logger.error("DeepLTranslator->%s: %s", exc, text)
        agent.log = f"{timezone_module.now()}: {str(exc)}"
    finally:
        save_func()
    return {"text": translated_text, "characters": len(text)}
