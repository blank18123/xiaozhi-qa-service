import os
import yaml
from collections.abc import Mapping


def get_project_dir():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + "/"


def read_config(config_path):
    with open(config_path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    return config


def deep_merge(base, override):
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], Mapping) and isinstance(value, Mapping):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config():
    from core.utils.cache.manager import cache_manager, CacheType
    cached_config = cache_manager.get(CacheType.CONFIG, "main_config")
    if cached_config is not None:
        return cached_config
    project_dir = get_project_dir()
    default_config_path = os.path.join(project_dir, "config.yaml")
    custom_config_path = os.path.join(project_dir, "data", ".config.yaml")
    default_config = read_config(default_config_path)
    if os.path.exists(custom_config_path):
        custom_config = read_config(custom_config_path)
        config = deep_merge(default_config, custom_config)
    else:
        config = default_config
    ensure_directories(config)
    cache_manager.set(CacheType.CONFIG, "main_config", config)
    return config


def ensure_directories(config):
    dirs = [
        config.get("TTS", {}).get("default", {}).get("output_dir", "tmp/"),
        config.get("ASR", {}).get("default", {}).get("output_dir", "tmp/"),
        "tmp/",
        "data/",
    ]
    for d in dirs:
        if d and not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
