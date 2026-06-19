from whaleyeah.plugins.openai_compatible import get_handler as get_compatible_handler


def get_handler(config: dict):
    compatible_config = dict(config)
    compatible_config.setdefault("command", "openai")
    return get_compatible_handler(compatible_config)
