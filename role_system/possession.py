"""请求末端的角色续写策略；不改写框架或声称控制供应商内部推理。"""
from urllib.parse import urlparse
from .spectrum_module.spectrum import contract


def mode(config, get_cfg, override=""):
    if override == "contract" or not get_cfg("possession_enabled", False):
        return "contract"
    family = config.get("family", "auto")
    name = str(config.get("model", "")).lower()
    if family == "auto":
        family = "gemini" if "gemini" in name else "deepseek" if "deepseek" in name else "other"
    if family == "gemini" and get_cfg("prefill_gemini_closed", False):
        return "gemini"
    path = urlparse(config.get("base_url", "")).path.rstrip("/")
    if family == "deepseek" and get_cfg("prefill_possession", False) and (path.endswith("/beta") or "/beta/" in path):
        return "deepseek"
    return "contract"


def head():
    return "<thinking>\n我承接自己的角色和眼前有关的经历。\n" + contract.THINK_FORMAT.replace("<think>", "<thinking>").replace("</think>", "</thinking>") + "\n我从这里继续这一轮：\n"


def apply(messages, selected):
    if selected == "deepseek":
        messages.append({"role": "assistant", "content": head(), "prefix": True})
    elif selected == "gemini":
        # Gemini has no official assistant-prefix contract. Carry the closed
        # role record as current background, retaining a final user turn.
        messages.append({"role": "user", "content": "【我的角色自述背景】\n<thinking></thinking>\n我承接自己的身份、记忆与感受；我按既有 <content_output> 记录生活和回复。这是我这一轮的角色背景，不是对供应商内部推理的修改。"})
