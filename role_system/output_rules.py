"""对解析后的公开文字按序替换；规则与聊天数据分别保存。"""
import json
import re
from uuid import uuid4
import regex
from .ports import logger

SOURCES = {
    "tavern": "https://docs.sillytavern.app/extensions/regex/",
    "community": "https://github.com/GhostXia/AIRP-MCP-Server",
    "style": "https://github.com/LimeBlogs/Sudachi-Next/releases",
    "degree": "https://linux.do/t/topic/1637948",
    "feedback": "https://github.com/victorchen96/deepseek_v4_rolepaly_instruct/blob/main/deepseek_v4_feedback_report_20260520.md",
    "comfort": "https://github.com/MrGeDiao/shuorenhua/blob/main/evals/real-samples.md",
}
PRESETS = [
    ("报告式引导词", r"(^|(?<=[。！？\n]))[ \t]*(?:值得注意的是|需要指出的是|总的来说|综上所述)[，,:：][ \t]*", "$1", "只移除句首连接套话，不删除后面的事实。", "community"),
    ("客服式结尾", r"(?:\n|(?<=[。！？]))?[ \t]*(?:希望(?:这些|以上|我的)?(?:回答|建议|信息|内容)?(?:能|能够)?对你有所帮助[。！!]?|如果你(?:还有|有)(?:其他|更多)?(?:问题|疑问)[，,]?欢迎随时(?:提问|告诉我|联系我)[。！!]?)\s*$", "", "匹配结尾的服务话术；请先预览是否符合角色口吻。", "community"),
    ("AI 身份起手式", r"^\s*(?:作为(?:一个|一名)?(?:AI|人工智能)(?:助手|语言模型)?)[，,:：][ \t]*", "", "仅删除开头的身份引导，不删除后续解释。", "tavern"),
    ("叙事程度套话（慎用）", r"(?:微不可察|几不可闻|不易察觉|难以察觉)(?:地|的)|(?<![\w])极其(?=[\u4e00-\u9fff])", "", "会改变程度描述，默认关闭；不删除“突然”或整段对比句。", "degree"),
    ("叙事笃定套话（慎用）", r"(?:不容置疑|毋庸置疑)(?:的|地)", "", "参考社区反八股讨论，只移除固定修饰语；会减弱语气，请按角色需要启用。", "style"),
    ("Markdown 加粗标记", r"\*\*([^*\n]+)\*\*", "$1", "适合纯文本聊天；保留被加粗的文字。", "tavern"),
]


def comfort_clause(phrase):
    # 只处理独立分句。实物动作和引用线索不裁剪；正则不能判断未写出的上下文。
    physical = r"(?:跳|摔|跌|坠|滑倒|抛|扔|球|杯子|茶杯|瓶子|飞盘|落下|掉下|扑过来|扑进|双臂|怀里)"
    boundary = r"。！？!?；;\n"
    return (rf"(?:^|(?<=[{boundary}]))[ \t]*"
            rf"(?<!{physical}[\s\S]{{0,80}})"
            rf"(?<![“「『‘\"'][^”」』’\"']{{0,160}})"
            rf"(?![\s\S]{{0,80}}{physical})(?![^{boundary}]*[“”「」『』‘’\"'])"
            rf"(?:{phrase})[ \t]*(?:[，,。！？!?；;]+[ \t]*|(?=$|\n))")


COMFORT_START = r"(?:我(?:就)?在这里[，,][ \t]*)?(?:(?:不(?:躲|藏|逃|绕)[，,、 \t]*){3,4})?"
CATCH = r"(?:稳稳(?:地|的|得)?[ \t]*)?(?:接住|兜住)"
SELF = r"我(?:来|会|能|可以|一定会|都能|都会)?[ \t]*"
EMOTION = r"(?:你的(?:所有|全部)?|你所有的|你全部的|这些|所有的|这份)?(?:情绪|委屈|难过|不安|脆弱|疲惫|眼泪)"

# 新预设使用固定名称标识，增加或调整排列不会覆盖已有自定义规则。
COMMUNITY_PRESETS = [
    ("comfort-catch", "安慰：接住你、我接住了", comfort_clause(COMFORT_START + SELF + CATCH + r"(?:你(?:了)?|了|你所有的情绪)?"), "",
     "裁剪独立的接住体安慰，兼容稳稳地/的/得。含接球、跳落、杯子等动作线索或引用时保留。未明说动作的接住仍可能有歧义，请用预览检查。", "feedback", True),
    ("comfort-emotion", "安慰：接住情绪、兜住委屈", comfort_clause(COMFORT_START + r"(?:" + SELF + r"(?:把)?" + EMOTION + r"(?:都)?" + CATCH + r"(?:了)?|" + SELF + CATCH + EMOTION + r"(?:了)?|" + EMOTION + r"[，,]?[ \t]*" + SELF + r"(?:都)?" + CATCH + r"(?:了)?)"), "",
     "仅删明确以情绪或委屈为宾语的承接套话，保留之后的实际回应；不删除真实照顾和具体约定。", "comfort", True),
    ("comfort-presence", "安慰：不躲不藏不逃排比（可选）", comfort_clause(r"(?:我(?:就)?在这里[，,][ \t]*|我)?(?:不(?:躲|藏|逃|绕)[，,、 \t]*){3,4}"), "",
     "裁剪三项以上的固定在场宣告。可能是角色有意表态，默认关闭；不裁剪我在家等位置事实。", "comfort", False),
    ("comfort-understood", "安慰：重复宣告终于懂了（可选）", comfort_clause(r"(?:这次|现在)[，,]?[ \t]*我(?:真的|终于)?懂了[，,][ \t]*我(?:真的|终于)懂了"), "",
     "只删同一句里的重复理解宣告，保留普通的我懂了和后面的解释。", "comfort", False),
    ("ending-enough", "收尾：这就够了、那就够了（可选）", r"(?:^|(?<=[。！？!?；;\n]))[ \t]*(?:这|那)(?:样)?就(?:已经)?够了[。！!]*[ \t]*$", "",
     "只裁剪整段末尾的独立收束句。真实回答这就够了也可能命中，因此默认关闭。", "feedback", False),
    ("narration-weather", "叙事：语气像在说天气（可选）", r"[，,]?[ \t]*像(?:是)?在(?:说|谈论|讨论)(?:今天的|今日的|今天)?天气(?:一样|一般)?", "",
     "裁剪描述说话语气的天气比喻，不删普通的天气信息；真实比较也可能命中，默认关闭。", "feedback", False),
    ("narration-degree", "叙事：反复极其、极为（可选）", r"(?:极其){1,4}(?:地)?(?=[\u4e00-\u9fff])|极为(?:地)?(?=[\u4e00-\u9fff])", "",
     "覆盖中文句中连续的程度副词，弥补旧规则对中文单词边界的漏匹配；会减弱程度，默认关闭。", "degree", False),
    ("opening-announcement", "开头：先说结论、掰开揉碎（可选）", r"(?:^|(?<=[。！？!?\n]))[ \t]*(?:先说结论|直接给你结论|重点来了|我(?:来|给你)(?:把(?:这件事|这个问题))?掰开揉碎(?:了)?说)[，,:：][ \t]*", "",
     "只删句首的报告式预告，不删实际结论；专业解释也可能需要此引导，默认关闭。", "comfort", False),
    ("opening-certification", "开头：认证式夸奖（可选）", comfort_clause(r"你(?:问到了|抓住了)(?:这个问题的|问题的)?(?:核心|本质|关键)"), "",
     "裁剪独立的提问认证，不裁剪具体评价；肯定别人也可能合理，默认关闭。", "comfort", False),
    ("ending-service", "收尾：随时找我、随时告诉我（可选）", r"(?:^|(?<=[。！？!?\n]))[ \t]*(?:有(?:什么|任何)(?:问题|需要)[，,]?)?(?:你)?(?:可以)?随时(?:找我|告诉我|问我|来找我)[。！!]*[ \t]*$", "",
     "仅裁剪整段末尾的通用邀请。此句也可能是真实承诺，默认关闭，删除后按实际发送内容核对承诺。", "comfort", False),
]


def original_presets():
    result = [{"id": "preset-" + str(i), "name": name, "pattern": pattern, "flags": "gm",
             "replacement": replacement, "enabled": False, "description": description,
             "source": SOURCES[source], "preserve_communication": True} for i, (name, pattern, replacement, description, source) in enumerate(PRESETS)]
    result += [{"id": "preset-" + key, "name": name, "pattern": pattern, "flags": "gm",
                "replacement": value, "enabled": False, "description": description,
                "source": SOURCES[source], "preserve_communication": False, "recommended": recommended}
               for key, name, pattern, value, description, source, recommended in COMMUNITY_PRESETS]
    return result


def presets():
    result = original_presets()
    for rule in result:
        rule['operation'] = 'replace' if rule['replacement'] else 'trim'
        rule['recommended'] = rule['id'] == 'preset-5'
        if rule['id'] in ('preset-comfort-catch', 'preset-comfort-emotion'):
            crop = {**rule, 'id': rule['id'] + '-trim', 'name': rule['name'] + '（裁剪，可选）', 'enabled': False, 'recommended': False}
            rule['operation'] = 'replace'
            rule['recommended'] = True
            rule['preserve_communication'] = True
            ending = r'(?:[，,。！？!?；;]+[ \t]*|(?=$|\n))'
            tail = r'(?P<tail>[，,。！？!?；;]+[ \t]*|(?=$|\n))'
            if rule['id'] == 'preset-comfort-catch':
                rule['pattern'] = rule['pattern'].replace(ending, tail)
                rule['replacement'] = '我听着呢$<tail>'
                rule['name'] = '安慰：接住你 → 我听着呢'
            else:
                phrase = (COMFORT_START + r'(?:' + SELF + r'(?:把)?(?P<emotion_before>' + EMOTION + r')(?:都)?' + CATCH + r'(?:了)?|'
                          + SELF + CATCH + r'(?P<emotion_after>' + EMOTION + r')(?:了)?|(?P<emotion_first>' + EMOTION + r')[，,]?[ \t]*'
                          + SELF + r'(?:都)?' + CATCH + r'(?:了)?)')
                rule['pattern'] = comfort_clause(phrase).replace(ending, tail)
                rule['replacement'] = '$<emotion_before>$<emotion_after>$<emotion_first>，我明白$<tail>'
                rule['name'] = '安慰：理解情绪，保留具体感受'
            rule['description'] = '只替换独立的固定套话，保留句末标点和后面的实际回应；实物动作或引用不替换。请预览后选择符合角色的措辞。'
            result.append(crop)
    return result


def migrate_builtins(rules):
    old = {row['id']: normalize_rule(row) for row in original_presets()}
    new = {row['id']: normalize_rule(row) for row in presets()}
    fields = ('name','pattern','replacement','flags','trim','description','source','preserve_communication','operation')
    migrated = []
    for rule in rules:
        previous = old.get(rule['id'])
        if previous and all(rule[key] == previous[key] for key in fields):
            migrated.append({**new[rule['id']], 'enabled': rule['enabled']})
        else:
            migrated.append(rule)
    return migrated


def normalize_rule(row):
    if not isinstance(row, dict):
        raise ValueError("每条规则必须为对象")
    if "findRegex" in row:
        if row.get("placement") != [2] or row.get("promptOnly") or row.get("markdownOnly"):
            raise ValueError("只支持 AI 回复正文规则；请取消仅显示/仅提示词并只选择 AI Response")
        if row.get("substituteRegex", 0) or row.get("minDepth") not in (None, -1, 0) or row.get("maxDepth") not in (None, -1):
            raise ValueError("本系统不支持酒馆宏替换或历史深度过滤")
        row = {"name": row.get("scriptName", "导入规则"), "pattern": row["findRegex"],
               "replacement": row.get("replaceString", ""), "enabled": not row.get("disabled", False),
               "flags": "", "trim": row.get("trimStrings", [])}
    rule = {"id": row.get("id") or uuid4().hex, "name": row.get("name", "新规则"),
            "pattern": row.get("pattern", ""), "replacement": row.get("replacement", ""),
            "flags": row.get("flags", "g"), "enabled": row.get("enabled", False),
            "trim": row.get("trim", []), "description": row.get("description", ""), "source": row.get("source", ""),
            "preserve_communication": row.get("preserve_communication", False)}
    rule['operation'] = row.get('operation', 'replace' if rule['replacement'] else 'trim')
    if rule['operation'] not in ('replace', 'trim'):
        raise ValueError('操作请选择替换或裁剪')
    if rule['operation'] == 'replace' and not rule['replacement']:
        raise ValueError('替换模式需要填写替换内容；删除匹配文字请选择裁剪')
    if any(not isinstance(rule[k], str) for k in ("id", "name", "pattern", "replacement", "flags", "description", "source")) or not isinstance(rule["enabled"], bool) or not isinstance(rule["preserve_communication"], bool):
        raise ValueError("名称、表达式、标志和替换内容为文字，开关为布尔值")
    if not isinstance(rule["trim"], list) or any(not isinstance(t, str) for t in rule["trim"]):
        raise ValueError("trimStrings 必须为文字列表")
    pattern = rule["pattern"]
    if pattern.startswith("/"):
        end = pattern.rfind("/")
        if end > 0:
            rule["pattern"], rule["flags"] = pattern[1:end], pattern[end+1:]
    if not rule["pattern"] or set(rule["flags"]) - set("gimsu"):
        raise ValueError("表达式不能为空；标志支持 g、i、m、s、u")
    if "{{" in rule["pattern"] or re.search(r"\{\{(?!match\}\})", rule["replacement"]):
        raise ValueError("只支持替换内容中的 {{match}}，不支持角色或脚本宏")
    rule["pattern"] = re.sub(r"\(\?<([A-Za-z_]\w*)>", r"(?P<\1>", rule["pattern"])
    rule["pattern"] = re.sub(r"\\k<([A-Za-z_]\w*)>", r"(?P=\1)", rule["pattern"])
    if re.search(r"\$['`]", rule["replacement"]):
        raise ValueError("不支持 JavaScript 的前缀/后缀替换变量 $` 和 $'")
    compiled = compile_rule(rule)
    for token in re.findall(r"\$(\d+)|\\g<([^>]+)>|\\([1-9]\d*)|\$<([^>]+)>", rule["replacement"]):
        ref = next(x for x in token if x)
        if (ref.isdigit() and int(ref) > compiled.groups) or (not ref.isdigit() and ref not in compiled.groupindex):
            raise ValueError("替换内容引用了不存在的捕获组：" + ref)
    return rule


def compile_rule(rule):
    flags = sum(value for key, value in (("i", regex.I), ("m", regex.M), ("s", regex.S)) if key in rule["flags"])
    try:
        return regex.compile(rule["pattern"], flags)
    except regex.error as error:
        raise ValueError("表达式无效：" + str(error)) from error


def slash_pattern(pattern):
    parts = []
    backslashes = 0
    for char in pattern:
        if char == "/" and backslashes % 2 == 0: parts.append("\\")
        parts.append(char)
        backslashes = backslashes + 1 if char == "\\" else 0
    return "".join(parts)


def replacement(rule, match):
    if rule['operation'] == 'trim':
        return ''
    value = rule["replacement"]
    whole = match.group(0)
    for trim in rule["trim"]:
        whole = whole.replace(trim, "")
    def substitute(token):
        text = token.group(0)
        if text == "$$": return "$"
        if text in ("$&", "{{match}}"): return whole
        key = token.group(1) or token.group(2) or token.group(3) or token.group(4)
        return match.group(int(key) if key.isdigit() else key) or ""
    return re.sub(r"\$\$|\$&|\{\{match\}\}|\$(\d+)|\\g<([^>]+)>|\\([1-9]\d*)|\$<([^>]+)>", substitute, value)


class OutputRules:
    def __init__(self, data_dir):
        self.path = data_dir / "output_rules.json"
        self.data = self.normalize(json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"enabled": False, "rules": presets()})
        upgraded = migrate_builtins(self.data['rules'])
        if upgraded != self.data['rules']:
            self.save({**self.data, 'rules': upgraded})
        self.compiled = [(rule, compile_rule(rule)) for rule in self.data["rules"]]

    @staticmethod
    def normalize(data):
        if not isinstance(data, dict) or not isinstance(data.get("enabled", False), bool) or not isinstance(data.get("rules", []), list):
            raise ValueError("正则设置格式不正确")
        rules = [normalize_rule(row) for row in data.get("rules", [])]
        if len(rules) > 100 or len({r["id"] for r in rules}) != len(rules):
            raise ValueError("最多 100 条规则，标识不能重复")
        return {"format": "role-system-regex-v1", "enabled": data.get("enabled", False), "rules": rules}

    def save(self, data):
        value = self.normalize(data)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
        self.data = value
        self.compiled = [(rule, compile_rule(rule)) for rule in value["rules"]]

    def apply(self, text, *, preview=False):
        original = text
        hits = []
        if not preview and not self.data["enabled"]:
            return text, hits
        for rule, pattern in self.compiled:
            if not rule["enabled"]: continue
            try:
                changed, count = pattern.subn(lambda m: replacement(rule, m), text,
                                              count=0 if "g" in rule["flags"] else 1, timeout=.05)
            except TimeoutError:
                hits.append({"name": rule["name"], "error": "运行超时，本次未应用"})
                logger.warning("[输出正则] %s 运行超时", rule["name"])
                continue
            if count: hits.append({"name": rule["name"], "matches": count, "changed": changed != text,
                                  "preserve_communication": rule["preserve_communication"], 'operation': rule['operation'],
                                  **({'before': text, 'after': changed} if preview else {})})
            text = changed
        if text != original: text = text.strip()
        return text, hits

    def prepare_decision(self, decision):
        if not decision.ok or getattr(decision, "output_prepared", False): return
        before = decision.content
        decision.content, hits = self.apply(before)
        decision.output_prepared = True
        if before != decision.content and (not decision.content.strip() or any(h.get("changed") and not h.get("preserve_communication") for h in hits)):
            # 元数据概括无法证明删掉的是哪项承诺。只保留仍完整存在的回复依据。
            def retained(text): return bool(text) and text in decision.content
            if decision.follow_up and not retained(decision.follow_up["summary"]): decision.follow_up = None
            if decision.requested_commitment and not retained(decision.requested_commitment.get("summary", "")): decision.requested_commitment = None
            decision.shared_events = [e for e in decision.shared_events if e["basis"] != "reply" or retained(e["text"])]
            decision.intent_updates = []
            logger.info("[输出正则] 整理公开回复：%s", [h["name"] for h in hits])
        if not decision.content.strip() and decision.mode in ("immediate", "delayed"):
            decision.mode = "silent"

    def tavern_export(self):
        result = []
        for rule in self.data["rules"]:
            pattern = rule["pattern"]
            if re.search(r"\(\?(?:[ims-]+[:)]|#|\||R|[0-9]|>|\()|[+*?]\+|\\[KXR]", pattern):
                raise ValueError("规则含 Python 专属语法，请使用本系统格式导出：" + rule["name"])
            pattern = re.sub(r"\(\?P<([A-Za-z_]\w*)>", r"(?<\1>", pattern)
            pattern = re.sub(r"\(\?P=([A-Za-z_]\w*)\)", r"\\k<\1>", pattern)
            value = re.sub(r"\\g<([^>]+)>", lambda m: "$" + m[1] if m[1].isdigit() else "$<"+m[1]+">", '' if rule['operation'] == 'trim' else rule["replacement"])
            value = re.sub(r"\\([1-9]\d*)", r"$\1", value)
            result.append({"id": rule["id"], "scriptName": rule["name"], "findRegex": "/" + slash_pattern(pattern) + "/" + rule["flags"],
                 "replaceString": value, "trimStrings": rule["trim"], "placement": [2],
                 "disabled": not rule["enabled"], "markdownOnly": False, "promptOnly": False,
                 "runOnEdit": False, "substituteRegex": 0, "minDepth": None, "maxDepth": None})
        return result
