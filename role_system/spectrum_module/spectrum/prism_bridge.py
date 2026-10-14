"""显式连接可选棱镜，背景情绪与客观事实分别保留。"""
_prism = None

def bind(prism):
    global _prism
    _prism = prism

def current():
    return _prism

def peek_signals(umo=""):
    return _prism.last_relation_signals(umo=umo) if _prism and _prism._cfg("enabled", True) else None

def peek_emotion(umo=""):
    return _prism.last_emotion_valence(umo=umo) if _prism and _prism._cfg("enabled", True) else None
