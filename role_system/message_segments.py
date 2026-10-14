"""按自然停顿发送完整文字；代码、网址和颜文字保持完整，媒体顺序不变。"""
import re
from .ports import Plain, MessageChain

# 符号括号中的标点和空格属于颜文字；相连的招呼及装饰一起发送。
KAOMOJI = r'(?:[A-Za-z]+[~～])?[（(][^()（）\n\u4e00-\u9fffA-Za-z0-9]{1,40}[)）][⌒☆★♡♥♪♫]*'
ATOMIC = re.compile(r'```[\s\S]*?```|`[^`\n]+`|https?://[A-Za-z0-9:/?#\[\]@!$&\x27()*+,;=%._~-]+|' + KAOMOJI)
ENDING = set('。？！!?…')
SOFT = set('；;，,：:、 \t')
CLOSERS = set('”’」』"\x27）)]')


def split_message(text, max_chars=55):
    maximum = max(16, int(max_chars))
    spans = list(ATOMIC.finditer(text))
    pieces = []
    start, index = 0, 0
    while index < len(text):
        atom = next((m for m in spans if m.start() == index), None)
        if atom:
            index = atom.end()
        else:
            index += 1
            if text[index - 1] in ENDING or text[index - 1] == '\n':
                while index < len(text) and (text[index] in ENDING or text[index] in CLOSERS):
                    index += 1
                value = text[start:index].strip()
                if value:
                    pieces.append(value)
                start = index
                continue
        if index - start >= maximum:
            end = index
            candidates = [i + 1 for i in range(start, min(start + maximum, len(text)))
                          if text[i] in SOFT and not any(m.start() <= i < m.end() for m in spans)]
            if candidates and candidates[-1] - start >= maximum // 3:
                end = candidates[-1]
            elif atom and atom.start() > start:
                end = atom.start()
            elif not atom:
                word = re.match(r'[A-Za-z0-9_-]+', text[end:])
                if word and text[end - 1].isascii() and text[end - 1].isalnum():
                    end += len(word.group())
            value = text[start:end].strip()
            if value:
                pieces.append(value)
            start = index = end
    tail = text[start:].strip()
    if tail:
        pieces.append(tail)
    return pieces


def message_batches(chain, enabled=True, max_chars=55):
    if not enabled:
        parts = [Plain(p.text.strip()) if isinstance(p, Plain) else p for p in chain.chain
                 if not isinstance(p, Plain) or p.text.strip()]
        return [MessageChain(parts)] if parts else []
    result, current = [], []
    for part in chain.chain:
        if not isinstance(part, Plain):
            current.append(part)
            continue
        for text in split_message(part.text, max_chars):
            # 聊天消息的边界已经表示停顿；网址等完整片段中的逗号仍属于内容。
            if not any(atom.end() == len(text) for atom in ATOMIC.finditer(text)):
                text = text.rstrip('，, \t')
            if not text:
                continue
            if any(isinstance(p, Plain) for p in current):
                result.append(MessageChain(current))
                current = []
            current.append(Plain(text))
    if current:
        result.append(MessageChain(current))
    return result
