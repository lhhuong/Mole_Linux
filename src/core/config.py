"""Versioned configuration; reading defaults does not write during preview."""
import json

DEFAULTS = {'version': 1, 'cache_age_days': 14, 'language': 'vi'}


def load(path):
    if not path.exists():
        return dict(DEFAULTS)
    config = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(config, dict) or type(config.get('version')) is not int or config['version'] != 1:
        raise ValueError('Unsupported MoleLinux configuration version')
    result = DEFAULTS | config
    if type(result['cache_age_days']) is not int or result['cache_age_days'] < 1:
        raise ValueError('cache_age_days must be a positive integer')
    if result['language'] not in ('vi', 'en'):
        raise ValueError('language must be vi or en')
    return result
