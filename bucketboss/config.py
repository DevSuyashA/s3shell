import copy
import json
import os

DEFAULT_CONFIG = {
    "general": {
        "workers": 16,
        "cache_ttl": 21600,
        "crawl_depth": 2,
        "verbose": False,
    }
}


def load_config(config_path=None):
    """Load config from file, merge with defaults."""
    if config_path is None:
        config_path = os.path.join(os.path.expanduser("~"), ".bucketboss", "config.json")

    config = copy.deepcopy(DEFAULT_CONFIG)

    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                user_config = json.load(f)
            # Merge user config over defaults
            for section, values in user_config.items():
                if section in config and isinstance(config[section], dict) and isinstance(values, dict):
                    config[section].update(values)
                else:
                    config[section] = values
        except (json.JSONDecodeError, OSError) as e:
            import sys
            print(f"Warning: Could not load config from {config_path}: {e}", file=sys.stderr)

    return config


def get_workers(config):
    """Get worker count from config."""
    return config.get("general", {}).get("workers", 16)
