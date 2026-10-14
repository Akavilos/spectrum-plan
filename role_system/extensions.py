"""实例级扩展注册、持久化依赖和原生 NoneBot 插件加载。"""
import asyncio
import importlib
import importlib.util
import inspect
import json
import re
import shutil
import sys
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from .host import Config
from .ports import logger


def plugin_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value):
        raise ValueError("插件标识使用小写字母、数字、下划线或连字符")
    return value


def entry_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", value):
        raise ValueError("入口填写 Python 模块名，例如 nonebot_plugin_xxx 或 plugin")
    return value


def check_settings(schema, values):
    if not isinstance(values, dict): raise ValueError("插件设置必须为 JSON 对象")
    if not schema: return
    for key, value in values.items():
        if key not in schema: raise ValueError("未知插件设置：" + key)
        field = schema[key]
        expected = {"string": str, "text": str, "boolean": bool, "integer": int,
                    "number": (int, float), "array": list, "object": dict}.get(field.get("type"))
        if expected is None or not isinstance(value, expected) or (field.get("type") in ("integer", "number") and isinstance(value, bool)):
            raise ValueError("插件设置类型不正确：" + key)
        if "enum" in field and value not in field["enum"]: raise ValueError("插件设置不在可选范围：" + key)
        if field.get("type") in ("integer", "number"):
            if "minimum" in field and value < field["minimum"]: raise ValueError("插件设置低于最小值：" + key)
            if "maximum" in field and value > field["maximum"]: raise ValueError("插件设置超过最大值：" + key)


class ExtensionAPI:
    def __init__(self, manager, pid):
        self.manager, self.id, self.host = manager, pid, manager.host
        self.config = manager.settings(pid)
        self.data_dir = manager.root / pid / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.tools = []

    def on(self, name, callback):
        if name not in ("startup", "shutdown", "before_request", "after_send"):
            raise ValueError("支持 startup、shutdown、before_request、after_send")
        self.manager.hooks[name].append((self.id, callback))

    def tool(self, tool):
        self.host.tools.register(tool)
        self.tools.append(tool)

    def command(self, name, callback):
        if not name or " " in name or name in self.manager.host.system.commands or name in self.manager.commands:
            raise ValueError("指令名称为空或已被使用")
        self.manager.commands[name] = (self.id, callback)

    async def send(self, session, chain):
        return await self.host.send_message(session, chain)


class Extensions:
    def __init__(self, host):
        self.host = host
        self.root = host.data_dir / "extensions"
        self.root.mkdir(exist_ok=True)
        self.registry = Config(self.root / "registry.json")
        self.registry.setdefault("plugins", {})
        self.running = {}
        self.applied = {}
        self.errors = {}
        self.apis = {}
        self.hooks = {key: [] for key in ("startup", "shutdown", "before_request", "after_send")}
        self.commands = {}
        self.busy = asyncio.Lock()
        self.deps = self.root / "dependencies"
        self.deps.mkdir(exist_ok=True)

    def settings(self, pid):
        item = self.registry["plugins"][plugin_id(pid)]
        directory = self.root / pid
        directory.mkdir(exist_ok=True)
        config = Config(directory / "config.json")
        for key, field in item.get("schema", {}).items():
            if "default" in field: config.setdefault(key, field["default"])
        return config

    def rows(self):
        return [{**item, "id": pid, "settings": dict(self.settings(pid)), "loaded": pid in self.running,
                 "error": self.errors.get(pid, ""), "pending_restart": item != self.applied.get(pid)}
                for pid, item in self.registry["plugins"].items()]

    async def dependencies(self, requirements):
        if not requirements: return
        if any(not isinstance(r, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?(?:[=<>!~]+[A-Za-z0-9.*+-]+(?:,[=<>!~]+[A-Za-z0-9.*+-]+)*)?", r) for r in requirements):
            raise ValueError("依赖填写 PyPI 包名和版本，不支持命令参数、URL 或本地路径")
        # 保持宿主的已安装依赖版本，避免插件安装时升级框架。
        from importlib.metadata import distributions
        pins = self.root / "host-constraints.txt"
        pins.write_text("\n".join(f"{d.metadata['Name']}=={d.version}" for d in distributions() if d.metadata.get("Name")), encoding="utf-8")
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
            "--target", str(self.deps), "--upgrade", "-c", str(pins), *requirements,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        stdout, _ = await process.communicate()
        output = stdout.decode(errors="replace")
        if process.returncode:
            logger.error("[扩展安装] %s", output[-4000:])
            raise ValueError("依赖安装失败，详情见运行日志")
        logger.info("[扩展安装] 依赖已保存：%s", requirements)

    async def install_package(self, body):
        pid = plugin_id(body.get("id"))
        if pid in self.registry["plugins"]: raise ValueError("插件已存在，请先停用并卸载")
        entry = entry_name(body.get("entry"))
        package = body.get("package", "")
        if not isinstance(package, str) or not package.startswith("nonebot-plugin-"): raise ValueError("此入口安装 nonebot-plugin-* PyPI 包；自有模块请上传 ZIP")
        await self.dependencies([package])
        self.registry["plugins"][pid] = {"name": body.get("name") or pid, "kind": "nonebot", "entry": entry,
                                          "enabled": False, "requirements": [package], "schema": {}}
        self.registry.save_config()

    async def install_zip(self, content):
        with zipfile.ZipFile(BytesIO(content)) as archive, tempfile.TemporaryDirectory(dir=self.root) as directory:
            staging = Path(directory)
            if sum(i.file_size for i in archive.infolist()) > 32 * 1024 * 1024: raise ValueError("解压后插件包不能超过 32 MB")
            for item in archive.infolist():
                path = (staging / item.filename).resolve()
                if not path.is_relative_to(staging.resolve()) or "\\" in item.filename or (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("插件 ZIP 包含越界路径或链接")
            archive.extractall(staging)
            candidates = list(staging.glob("manifest.json")) or list(staging.glob("*/manifest.json"))
            if len(candidates) != 1: raise ValueError("ZIP 根目录或单层文件夹须有一个 manifest.json")
            manifest = json.loads(candidates[0].read_text(encoding="utf-8"))
            pid = plugin_id(manifest.get("id"))
            if pid in self.registry["plugins"]: raise ValueError("插件已存在，请先停用并卸载")
            entry = entry_name(manifest.get("entry", "plugin"))
            if manifest.get("kind", "module") not in ("module", "nonebot"): raise ValueError("插件类型支持 module 或 nonebot")
            schema = manifest.get("schema", {})
            if not isinstance(schema, dict) or any(not isinstance(v, dict) for v in schema.values()): raise ValueError("schema 使用设置名称与字段定义的对象")
            defaults = {k: v["default"] for k, v in schema.items() if "default" in v}
            check_settings(schema, defaults)
            code = candidates[0].parent
            entry_path = code.joinpath(*entry.split("."))
            if not entry_path.with_suffix(".py").exists() and not (entry_path / "__init__.py").exists(): raise ValueError("找不到入口模块文件")
            integration = manifest.get("integration_entry", "")
            if integration:
                if manifest.get("kind", "module") != "nonebot": raise ValueError("协作入口仅用于NoneBot原生插件")
                integration = entry_name(integration)
                if not integration.startswith(entry + "."): raise ValueError("协作入口须位于该插件自己的包内")
                integration_path = code.joinpath(*integration.split("."))
                if not integration_path.with_suffix(".py").exists(): raise ValueError("找不到声明的协作入口文件")
            await self.dependencies(manifest.get("requirements", []))
            destination = self.root / pid / "code"
            destination.parent.mkdir(exist_ok=True)
            if destination.exists(): shutil.rmtree(destination)
            shutil.copytree(code, destination)
            self.registry["plugins"][pid] = {"name": manifest.get("name", pid), "kind": manifest.get("kind", "module"),
                "entry": entry, "enabled": False, "requirements": manifest.get("requirements", []), "schema": schema,
                "description": manifest.get("description", "")}
            if integration:self.registry["plugins"][pid]["integration_entry"] = integration
            self.registry.save_config()
        return pid

    def remove(self, pid):
        pid = plugin_id(pid)
        if pid not in self.registry["plugins"]: raise ValueError("插件不存在")
        if pid in self.running: raise ValueError("先关闭插件并重启，然后再卸载")
        del self.registry["plugins"][pid]
        self.registry.save_config()
        code = self.root / pid / "code"
        if code.exists(): shutil.rmtree(code)

    def load(self, driver=None):
        sys.path.append(str(self.deps))
        for pid, item in self.registry["plugins"].items():
            self.applied[pid] = json.loads(json.dumps(item))
            if not item["enabled"]: continue
            api = ExtensionAPI(self, pid)
            try:
                if item["kind"] == "nonebot":
                    import nonebot
                    if driver is None: raise ValueError("原生插件需要 NoneBot 启动环境")
                    for key, value in api.config.items():
                        setattr(driver.config, key, value)
                    code = self.root / pid / "code"
                    if code.exists(): sys.path.append(str(code))
                    plugin = nonebot.load_plugin(item["entry"])
                    if plugin is None: raise ValueError("NoneBot 未能加载插件，详情见平台日志")
                    if item.get("integration_entry"):
                        integration = importlib.import_module(item["integration_entry"])
                        result = integration.setup(api)
                        if inspect.isawaitable(result): raise ValueError("协作入口setup使用普通函数")
                else:
                    code = self.root / pid / "code"
                    entry = code.joinpath(*item["entry"].split("."))
                    path = entry / "__init__.py" if entry.is_dir() else entry.with_suffix(".py")
                    spec = importlib.util.spec_from_file_location("role_extension_" + pid.replace("-", "_"), path,
                                submodule_search_locations=[str(path.parent)])
                    plugin = importlib.util.module_from_spec(spec)
                    sys.modules[spec.name] = plugin
                    spec.loader.exec_module(plugin)
                    result = plugin.setup(api)
                    if inspect.isawaitable(result): raise ValueError("setup 使用普通函数；异步初始化注册 startup 钩子")
                self.running[pid] = json.loads(json.dumps(item))
                self.apis[pid] = api
                logger.info("[扩展] %s 已加载", item["name"])
            except Exception as error:
                self.errors[pid] = str(error)
                for callbacks in self.hooks.values(): callbacks[:] = [h for h in callbacks if h[0] != pid]
                self.commands = {k: v for k, v in self.commands.items() if v[0] != pid}
                for tool in api.tools: self.host.tools.tools.remove(tool)
                logger.exception("[扩展] %s 加载失败", pid)

    async def emit(self, name, *args):
        for pid, callback in self.hooks[name]:
            try:
                result = callback(*args)
                if inspect.isawaitable(result): await result
            except Exception:
                logger.exception("[扩展] %s 的 %s 钩子失败", pid, name)

    async def command(self, name, event, arguments):
        if name not in self.commands: return False
        _, callback = self.commands[name]
        result = callback(event, arguments)
        if inspect.isawaitable(result): await result
        return True
