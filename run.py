"""启动原生机器人，利用标准进程管道保存控制台输出，不改写子进程输出流。"""
import json
import logging
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from role_system.runtime_logs import redact_text, config_credentials


def credentials(directory):
    """读取持久化的当前密钥；启动阶段还没有配置文件时使用环境密钥。"""
    values = [os.environ.get('PANEL_KEY', ''), os.environ.get('ONEBOT_ACCESS_TOKEN', '')]
    for name in ('system.json', 'panel-access.json', 'connections.json'):
        path = directory / name
        if not path.exists():
            continue
        config = json.loads(path.read_text(encoding='utf-8'))
        if name == 'panel-access.json': values.append(config.get('key', ''))
        elif name == 'connections.json': values.append(config.get('access_token', ''))
        else:
            values.extend(config_credentials(config))
    return values


def run(command=None):
    directory = Path(os.environ.get('ROLE_DATA_DIR', 'data/role-system'))
    if command is None:
        from role_system.maintenance import apply_pending
        apply_pending(directory)
    log_path = directory / 'logs/console.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    writer = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    writer.setFormatter(logging.Formatter('%(message)s'))
    process = subprocess.Popen(command or [sys.executable, '-u', 'bot.py'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               encoding='utf-8', errors='replace')
    previous = {}
    def forward(signum, frame):
        if process.poll() is None: process.send_signal(signum)
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.signal(signum, forward)

    def collect(stream, terminal, level):
        for line in stream:
            terminal.write(line)
            terminal.flush()
            text = line.rstrip('\r\n')
            if not text: continue
            row = {'time': datetime.now().astimezone().isoformat(timespec='seconds'),
                   'level': logging.getLevelName(level), 'source': 'console',
                   'message': redact_text(text, credentials(directory))}
            writer.handle(logging.LogRecord('console', level, '', 0, json.dumps(row, ensure_ascii=False), (), None))
        stream.close()

    readers = [threading.Thread(target=collect, args=(process.stdout, sys.stdout, logging.INFO), daemon=True),
               threading.Thread(target=collect, args=(process.stderr, sys.stderr, logging.ERROR), daemon=True)]
    for reader in readers: reader.start()
    try:
        result = process.wait()
        for reader in readers: reader.join(timeout=5)
        return result if result >= 0 else 128 - result
    finally:
        for signum, handler in previous.items(): signal.signal(signum, handler)
        writer.close()


if __name__ == '__main__': raise SystemExit(run())
