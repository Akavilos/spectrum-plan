"""网页管理密钥独立保存；轮换后旧登录凭证立即失效。"""
import json
import os
import re
import secrets


class PanelAccess:
    def __init__(self, directory, initial_key):
        self.path = directory / "panel-access.json"
        self.initial_key = initial_key

    @property
    def key(self):
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))["key"]
        return self.initial_key

    def verify(self, key):
        return isinstance(key, str) and secrets.compare_digest(key.encode("utf-8"), self.key.encode("utf-8"))

    def save(self, key):
        if not isinstance(key, str) or not re.fullmatch(r"[!-~]{8,128}", key):
            raise ValueError("管理密钥填写 8–128 位字母、数字或符号，不含空格")
        temporary = self.path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump({"key": key}, output, ensure_ascii=False)
        temporary.replace(self.path)
