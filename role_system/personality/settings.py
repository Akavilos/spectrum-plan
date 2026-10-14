"""人格设置的边界与可移植底稿格式。"""
import json

DEFAULTS = {'enabled': False, 'learning_enabled': False, 'provider_id': '', 'interval_hours': 6,
            'min_messages': 12, 'daily_budget': 4, 'batch_size': 40,
            'max_context': 3, 'context_chars': 1600}
RANGES = {'interval_hours': (1, 168), 'min_messages': (4, 100), 'daily_budget': (0, 24),
          'batch_size': (8, 100), 'max_context': (1, 6), 'context_chars': (300, 3000)}


def settings(values):
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise ValueError('人格设置包含未知选项')
    result = {**DEFAULTS, **values}
    if not isinstance(result['provider_id'], str): raise ValueError('整理模型使用已保存配置的名称')
    for key in ('enabled', 'learning_enabled'):
        if not isinstance(result[key], bool): raise ValueError('人格开关使用布尔值')
    for key, (low, high) in RANGES.items():
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f'{key} 需为 {low}–{high} 的整数')
    if result['batch_size'] < result['min_messages']:
        raise ValueError('每批条数不能少于开始学习所需条数')
    return result


def first_person(value, limit=240):
    if not isinstance(value, str) or not value.strip().startswith('我') or len(value.strip()) > limit:
        raise ValueError(f'认识用“我”开头，最多 {limit} 字')
    if any(token in value for token in ('<', '>', '\n')):
        raise ValueError('每条认识使用单行普通文字')
    return value.strip()


def base_lines(value):
    if not isinstance(value, list) or len(value) > 12: raise ValueError('底稿最多 12 条')
    return list(dict.fromkeys(first_person(line) for line in value))


class Settings:
    def __init__(self, directory):
        self.path = directory / 'config.json'
        raw = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        self.role_id = raw.pop('role_id', '')
        self.values = settings(raw)

    def save(self, values=None, role_id=None):
        if values is not None: self.values = settings(values)
        if role_id is not None: self.role_id = role_id
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({**self.values, 'role_id': self.role_id}, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

