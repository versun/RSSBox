def testagent_translate(agent, text, target_language, *, logger, sleep_func):
    logger.info(">>> Test Translate [%s]: %s", target_language, text)
    sleep_func(agent.interval)
    return {"text": agent.translated_text, "tokens": 10, "characters": len(text)}


def testagent_summarize(agent, text, target_language, *, logger, sleep_func):
    logger.info(">>> Test Summarize [%s]: %s", target_language, text)
    sleep_func(agent.interval)
    return {"text": agent.translated_text, "tokens": 10, "characters": len(text)}


def testagent_filter(agent, *, logger, sleep_func, random_choice):
    logger.info(">>> Test Filter")
    sleep_func(agent.interval)
    return {"passed": random_choice([True, False]), "tokens": 10}
