"""可移植配置交换和数据库在线快照，不导入伪造的聊天经历。"""
import copy
import json
import sqlite3
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from .extensions import check_settings
from .output_rules import OutputRules


def secret_field(key):
    name = key.lower().replace('-', '_')
    return name in {"key", "token", "authorization", "cookie", "credential"} or any(x in name for x in ("api_key", "password", "secret", "access_token", "panel_key")) or name.endswith("_token")


def public_config(value):
    if isinstance(value, list): return [public_config(v) for v in value]
    if not isinstance(value, dict): return value
    return {k: public_config(v) for k, v in value.items() if not secret_field(k)}


def export_data(host, system, section="all"):
    sections = {
        "settings": {"system": public_config(dict(host.config)), "modules": public_config(system.configs)},
        "persona": host.config["persona"],
        "personality": system.personality.export(),
        "worldbook": system.spectrum.store.worldbooks.export_library(),
        "regex": host.output_rules.data,
        "plugins": {pid: public_config(dict(host.extensions.settings(pid))) for pid in host.extensions.registry["plugins"]},
    }
    if section != "all":
        if section not in sections: raise ValueError("未知数据类别")
        sections = {section: sections[section]}
    return {"format": "role-system-data-v1", "sections": sections}


def validate_import(host, system, body):
    from .web import schemas, validate_config, validate_model, validate_provider_choices
    if not isinstance(body, dict) or body.get("format") != "role-system-data-v1" or not isinstance(body.get("sections"), dict):
        raise ValueError("请导入本系统导出的数据 JSON；酒馆正则在正则页单独导入")
    sections = copy.deepcopy(body["sections"])
    if set(sections) - {"settings", "persona", "personality", "worldbook", "regex", "plugins"}: raise ValueError("文件包含未知数据类别")
    if "personality" in sections: sections["personality"] = system.personality.validate_export(sections["personality"])
    if "persona" in sections and not isinstance(sections["persona"], str): raise ValueError("人设必须为文字")
    if "settings" in sections:
        settings = sections["settings"]
        if not isinstance(settings, dict) or set(settings) - {"system", "modules"}: raise ValueError("设置格式不正确")
        values = settings.get("system", {})
        if not isinstance(values, dict) or set(values) - {"enabled", "persona", "operators", "model", "embedding", "providers", "chat_provider_id", "provider_sources", "embedding_provider_id", "fallback_models", "active_memory", "speech"}: raise ValueError("未知系统设置")
        if 'speech' in values:
            from .speech import validate as validate_speech
            values['speech']=validate_speech(values['speech'])
        if 'active_memory' in values:
            from .active_memory.service import validate
            values['active_memory']=validate(values['active_memory'])
        from .model_catalog import validate_source
        sources = values.get('provider_sources', {})
        if not isinstance(sources, dict): raise ValueError('提供商设置格式错误')
        for source in sources.values(): validate_source(source)
        if "enabled" in values and not isinstance(values["enabled"], bool): raise ValueError("启用状态格式错误")
        if "persona" in values and not isinstance(values["persona"], str): raise ValueError("人设必须为文字")
        if "operators" in values and (not isinstance(values["operators"], list) or any(not str(x).isdigit() for x in values["operators"])): raise ValueError("操作员使用 QQ 号列表")
        if "model" in values: validate_model(values["model"])
        embedding = values.get("embedding", {})
        if not isinstance(embedding, dict) or set(embedding) - {"enabled", "base_url", "model", "api_key", "catalog_migrated"}: raise ValueError("未知嵌入设置")
        if not isinstance(embedding.get("enabled", False), bool): raise ValueError("嵌入开关必须为布尔值")
        validate_model({k: v for k, v in embedding.items() if k not in ("enabled", "catalog_migrated")})
        providers = values.get("providers", {})
        if not isinstance(providers, dict): raise ValueError("辅助模型设置格式错误")
        for name, provider in providers.items():
            if not name or name in ('role', 'default') or len(name) > 256: raise ValueError("模型配置名称不正确")
            validate_model(provider)
        source_choices = {**host.config.get('provider_sources', {}), **sources}
        for provider in [values.get('model', {}), *providers.values()]:
            if provider.get('source_id') and provider['source_id'] not in source_choices: raise ValueError('模型来源不存在')
        available = {**host.config['providers'], **providers}
        from .model_fallback import validate_choices
        proposed = {**host.config, **values, 'providers': available, 'provider_sources': source_choices}
        validate_choices(proposed, proposed.get('fallback_models', {}))
        validate_provider_choices(host, values, available)
        modules = settings.get("modules", {})
        if not isinstance(modules, dict) or set(modules) - set(system.configs): raise ValueError("未知核心模块")
        schema = schemas()
        for name, config in modules.items():
            if not isinstance(config, dict): raise ValueError("模块设置必须为对象")
            validate_config(schema[name], config)
            validate_provider_choices(host, config, available)
    available = {**host.config['providers'], **sections.get('settings', {}).get('system', {}).get('providers', {})}
    if 'personality' in sections:
        validate_provider_choices(host, sections['personality']['settings'], available)
    if "worldbook" in sections:
        from .spectrum_module.spectrum.worldbook_exchange import validate_library
        validate_library(sections['worldbook'],system.spectrum.store._WORLDBOOK_COLS)
    if "regex" in sections: sections["regex"] = OutputRules.normalize(sections["regex"])
    if "plugins" in sections:
        if not isinstance(sections["plugins"], dict): raise ValueError("插件设置格式错误")
        for pid, values in sections["plugins"].items():
            if pid not in host.extensions.registry["plugins"]: raise ValueError("先安装插件再导入其设置：" + pid)
            check_settings(host.extensions.registry["plugins"][pid].get("schema", {}), values)
    return sections


def merge_preserving_secrets(current, incoming):
    result = copy.deepcopy(current)
    for key, value in incoming.items():
        if secret_field(key):
            continue
        result[key] = merge_preserving_secrets(result.get(key, {}), value) if isinstance(value, dict) else value
    return result


def import_data(host, system, sections):
    changed = []
    if "settings" in sections:
        values = sections["settings"]
        merged = merge_preserving_secrets(dict(host.config), values.get("system", {}))
        host.config.clear(); host.config.update(merged); host.config.save_config()
        host.refresh_providers()
        for name, values in values.get("modules", {}).items():
            config = system.configs[name]
            merged = merge_preserving_secrets(dict(config), values)
            config.clear(); config.update(merged); config.save_config()
        from .prism_module.prism.settings import migrate
        migrate(system.configs['prism'])
        system.prism.personality = system.prism._load_personality()
        system.prism._apply_half_life()
        changed.append("settings")
    if "persona" in sections:
        host.config["persona"] = sections["persona"]; host.config.save_config()
        changed.append("persona")
    if "personality" in sections:
        system.personality.save(sections["personality"], confirm=True, source='导入可移植底稿')
        changed.append("personality")
    if "worldbook" in sections:
        store = system.spectrum.store
        store.worldbooks.import_library(sections['worldbook'])
        changed.append("worldbook")
    if "regex" in sections:
        host.output_rules.save(sections["regex"]); changed.append("regex")
    if "plugins" in sections:
        for pid, values in sections["plugins"].items():
            config = host.extensions.settings(pid)
            merged = merge_preserving_secrets(dict(config), values)
            config.clear(); config.update(merged); config.save_config()
            host.extensions.registry["plugins"][pid]["revision"] = host.extensions.registry["plugins"][pid].get("revision", 0) + 1
        host.extensions.registry.save_config(); changed.append("plugins")
    return changed


def snapshot(host, include_secrets=False):
    output = BytesIO()
    with tempfile.TemporaryDirectory() as directory, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in host.data_dir.rglob("*"):
            relative = path.relative_to(host.data_dir)
            if path.name == "panel-access.json" and not include_secrets:
                continue
            if not path.is_file() or path.is_symlink() or any(p in relative.parts for p in ("dependencies", "backups", "__pycache__")) or path.name.endswith(("-wal", "-shm", ".tmp")):
                continue
            if path.suffix in (".db", ".sqlite", ".sqlite3"):
                destination = Path(directory) / "snapshot.db"
                with sqlite3.connect(str(path)) as source, sqlite3.connect(destination) as target:
                    source.backup(target)
                archive.write(destination, str(relative)); destination.unlink()
            elif path.suffix == ".json" and not include_secrets:
                archive.writestr(str(relative), json.dumps(public_config(json.loads(path.read_text(encoding="utf-8"))), ensure_ascii=False))
            else:
                archive.write(path, str(relative))
        archive.writestr("BACKUP.json", json.dumps({"format": "role-system-backup-v1", "includes_secrets": include_secrets,
            "restore": "停止实例后恢复数据目录，再安装 registry.json 声明的依赖；请勿覆盖运行中的数据库。"}, ensure_ascii=False))
    return output.getvalue()
