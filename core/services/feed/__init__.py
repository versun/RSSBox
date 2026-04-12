from importlib import import_module


_EXPORTS = {
    "run_feed_update": ("core.services.feed.pipeline", "run_feed_update"),
    "refresh_updated_content": ("core.services.feed.refresh", "refresh_updated_content"),
    "render_feed_content": ("core.services.feed.rendering", "render_feed_content"),
    "render_tag_content": ("core.services.feed.rendering", "render_tag_content"),
    "build_feed_response": ("core.services.feed.response", "build_feed_response"),
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attribute_name = _EXPORTS[name]
    module = import_module(module_name)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value
