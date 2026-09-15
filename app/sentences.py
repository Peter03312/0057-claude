"""句界切分：只按 。！？ 划分句子。

规则：
- 句末符只有 。！？ 三个，其余标点（包括英文句点）不断句；
- 连续句末符归同一句（如 “太好了！！” 是一句）；
- 末尾未被句末符闭合的片段不计入句子。
"""
from __future__ import annotations

TERMINATORS = "。！？"


def split_sentences(text: str) -> list[str]:
    """把文本切成完整句子，丢弃末尾未闭合的片段。"""
    sentences: list[str] = []
    buf: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        buf.append(ch)
        if ch in TERMINATORS:
            # 连续句末符归并到当前句
            j = i + 1
            while j < n and text[j] in TERMINATORS:
                buf.append(text[j])
                j += 1
            sentences.append("".join(buf))
            buf = []
            i = j
        else:
            i += 1
    # buf 里剩下的内容是未闭合片段，不计入句子
    return sentences


def tail_sentences(text: str, count: int) -> list[str]:
    """取文本末尾的 count 个完整句子；不足时返回全部。"""
    if count <= 0:
        return []
    sentences = split_sentences(text)
    return sentences[-count:]
