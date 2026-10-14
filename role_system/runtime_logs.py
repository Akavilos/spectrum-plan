"""收集标准日志和平台日志，以限量轮转文件供认证后的控制台读取。"""
import json
import logging
import re
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


LEVELS = {"DEBUG": 10, "INFO": 20, "SUCCESS": 25, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


def config_credentials(config):
    """收集嵌套配置中的密钥，也解析引用的环境变量；不改变配置本身。"""
    import os
    from .data_exchange import secret_field
    values = []
    def visit(value, sensitive=False):
        if isinstance(value, dict):
            for key, item in value.items(): visit(item, sensitive or secret_field(key))
        elif isinstance(value, list):
            for item in value: visit(item, sensitive)
        elif sensitive and isinstance(value, str) and value:
            values.append(os.environ.get(value[1:], '') if value.startswith('$') else value)
    visit(config)
    return values


def redact_text(text, secrets):
    """标准日志与进程输出使用同一套密钥隐藏规则。"""
    for value in secrets:
        if value:
            text = text.replace(str(value), "[已隐藏密钥]")
    return re.sub(r'(?i)(authorization\s*[:=]\s*bearer\s+|(?:api_key|access_token|panel_key)\s*[\"\']?\s*[:=]\s*[\"\']?)[^\s\"\',}]+', r'\1[已隐藏密钥]', text)


class RuntimeLogs(logging.Handler):
    def __init__(self, directory, secrets):
        super().__init__(logging.DEBUG)
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "runtime.log"
        self.secrets = secrets
        self.writer = RotatingFileHandler(self.path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        self.writer.setFormatter(logging.Formatter("%(message)s"))
        self.exception_formatter = logging.Formatter()
        self.platform_logger = None
        self.platform_sink = None

    def start(self):
        # Uvicorn applies its logging configuration after the panel is constructed.
        root = logging.getLogger()
        if self not in root.handlers:
            root.addHandler(self)
        root.setLevel(logging.INFO)
        role = logging.getLogger("role_system")
        role.disabled = False
        role.propagate = True
        role.setLevel(logging.DEBUG)
        if self.platform_logger is not None and self.platform_sink is None:
            self.bind_platform(self.platform_logger)
        role.info("网页日志已连接：平台、角色模块与标准日志")

    def redact(self, text):
        return redact_text(text, self.secrets())

    def emit(self, record):
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + self.exception_formatter.formatException(record.exc_info)
        entry = {"time": datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="seconds"),
                 "level": record.levelname, "source": record.name, "message": self.redact(message)}
        output = logging.LogRecord("runtime_file", record.levelno, "", 0,
                                   json.dumps(entry, ensure_ascii=False), (), None)
        self.writer.handle(output)

    def bind_platform(self, logger):
        if self.platform_sink is not None:
            if self.platform_logger is logger:
                return
            self.platform_logger.remove(self.platform_sink)
        self.platform_logger = logger
        self.platform_sink = logger.add(self._platform_message, level="DEBUG", colorize=False, diagnose=False)

    def _platform_message(self, message):
        original = message.record
        record = logging.LogRecord(original["name"] or "nonebot", original["level"].no, "", 0,
                                   original["message"], (), original["exception"])
        record.created = original["time"].timestamp()
        record.levelname = original["level"].name
        self.handle(record)

    def read(self, *, limit=300, level="DEBUG", query="", source="runtime"):
        records = []
        # Flush/read under the same lock as rotation; older generations remain available after restart.
        self.writer.acquire()
        try:
            self.writer.flush()
            console = self.path.with_name("console.log")
            bases = {"runtime": [self.path], "console": [console], "all": [self.path, console]}[source]
            paths = [Path(str(base) + suffix) for base in bases for suffix in (".3", ".2", ".1", "")]
            for path in paths:
                try:
                    contents = path.read_bytes()
                except FileNotFoundError:
                    # 输出采集进程可能刚完成轮转；本次跳过该旧文件，下一次刷新读取新文件。
                    continue
                # 只读取以换行结束的完整记录，避免读取到并发写入的一部分 UTF-8 字节。
                for encoded in contents.split(b"\n")[:-1]:
                    line = encoded.decode("utf-8")
                    row = json.loads(line)
                    row["message"] = self.redact(row["message"])
                    if LEVELS.get(row["level"], 20) < LEVELS[level]:
                        continue
                    if query and query.casefold() not in (row["source"] + " " + row["message"]).casefold():
                        continue
                    records.append(row)
            return sorted(records, key=lambda row: row["time"])[-limit:]
        finally:
            self.writer.release()

    def close(self):
        logging.getLogger().removeHandler(self)
        if self.platform_sink is not None:
            self.platform_logger.remove(self.platform_sink)
            self.platform_sink = None
        self.writer.close()
        super().close()


def install_logs(host, panel_key):
    def credentials():
        import os
        return [host.panel_access.key, panel_key, os.environ.get("ONEBOT_ACCESS_TOKEN", ""), host.connections.values['access_token'],
                *config_credentials(host.config)]
    logs = RuntimeLogs(host.data_dir / "logs", credentials)
    logging.getLogger().addHandler(logs)
    logging.getLogger("role_system").setLevel(logging.INFO)
    return logs
