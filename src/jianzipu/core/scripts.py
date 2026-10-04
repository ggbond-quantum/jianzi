"""脚本变体归并：把同一语义字的简体/繁体/异体写法归入同一等价类。

字体编译（连字触发）与库访问（跨库检索）共用这里的等价键定义，
保证"什么算同一个字"两边始终一致。
"""

from zhconv import convert as zh_convert


def canonical_char(ch: str) -> str:
    """字的规范形式（优先繁体，沿用广陵神器编码）；无法转换时保持原字。"""
    traditional = zh_convert(ch, "zh-hant")
    return traditional if len(traditional) == 1 else ch


def equivalence_key(text: str) -> str:
    """编码的等价键：逐字取规范形式后拼接；键相同即视为等价的减字编码。"""
    return "".join(canonical_char(ch) for ch in text)


def simplified_char(ch: str) -> str:
    """字的简体形式；无法单字转换时保持原字。"""
    simplified = zh_convert(ch, "zh-hans")
    return simplified if len(simplified) == 1 else ch


def to_simplified(text: str) -> str:
    """逐条转换为简体形式（自定义库不变量，ADR 0006）。"""
    return "".join(simplified_char(ch) for ch in text)