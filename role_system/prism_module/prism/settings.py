"""迁移旧设置到分组，统一读取棱镜功能与人格配置。"""

from copy import deepcopy
import json
from pathlib import Path
import math

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "_conf_schema.json")
                    .read_text(encoding="utf-8-sig"))
GROUPS = {key: field["items"] for key, field in SCHEMA.items()
          if field["type"] == "object" and not field.get("invisible")}
FIELD_GROUP = {key: group for group, items in GROUPS.items() for key in items}
LAYOUT_VERSION = "settings_layout_version"


def validate_cares(raw):
    from .goals import GOALS
    values = json.loads(raw or '{}') if isinstance(raw, str) else raw
    if not isinstance(values, dict):
        raise ValueError('在意事项需填写JSON对象')
    for key, value in values.items():
        if key not in GOALS:
            raise ValueError('未知的在意目标：' + key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 3:
            raise ValueError('在意目标倍率需在0至3之间')
    return values


def migrate(config: dict) -> bool:
    """首次升级把旧字段复制到分组并保存布局版本；后续调用不再覆盖分组。"""
    if config.get(LAYOUT_VERSION, 0) >= 2:
        return False
    for name, fields in GROUPS.items():
        target = config.setdefault(name, {})
        for key, field in fields.items():
            if key in target:
                continue
            if key in config:
                target[key] = deepcopy(config[key])
            elif key not in target:
                target[key] = deepcopy(field.get("default"))
    config[LAYOUT_VERSION] = 2
    save = getattr(config, "save_config", None)
    if callable(save):
        save()
    return True


def value(config: dict, key: str, default=None):
    """布局迁移后读取分组值，旧布局读取平铺值；空值使用调用方默认。"""
    group = FIELD_GROUP.get(key)
    if config.get(LAYOUT_VERSION, 0) >= 1 and group is not None:
        result = config.get(group, {}).get(key, default)
    else:
        result = config.get(key, default)
    return default if result is None else result
