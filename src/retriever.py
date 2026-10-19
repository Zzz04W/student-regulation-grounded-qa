# -*- coding: utf-8 -*-
"""检索节点实现：面向中文条款的 BM25 检索器。

为什么不用向量：条款文本短、术语密集，字面匹配（BM25 over 字符 uni/bi-gram）
在本场景召回更稳、零依赖、结果完全可复现，便于回归测试。
"""
import json
import math
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHUNKS = os.path.join(BASE, "corpus", "chunks.jsonl")

STOP = set("的了和与及在为对由从向把被并等或有关以及对于关于是否何如何怎么什么哪些")
PUNCT = re.compile(r"[^\w\u4e00-\u9fff]+")


def tokenize(text: str) -> list:
    """中文按 uni/bi-gram 切分，英文数字按词切分。"""
    text = text.lower()
    toks = []
    for seg in PUNCT.split(text):
        if not seg:
            continue
        if re.search(r"[\u4e00-\u9fff]", seg):
            for i, ch in enumerate(seg):
                if ch in STOP:
                    continue
                toks.append(ch)
                if i + 1 < len(seg):
                    toks.append(seg[i:i + 2])
        else:
            toks.append(seg)
    return toks


class BM25:
    def __init__(self, chunks: list, k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.docs = [tokenize(c["text"]) for c in chunks]
        self.lens = [len(d) for d in self.docs]
        self.avg = sum(self.lens) / max(len(self.lens), 1)
        self.df = {}
        for d in self.docs:
            for t in set(d):
                self.df[t] = self.df.get(t, 0) + 1
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - df + 0.5) / (df + 0.5)) for t, df in self.df.items()}

    def score(self, query: str) -> list:
        q = tokenize(query)
        out = []
        for i, d in enumerate(self.docs):
            tf = {}
            for t in d:
                tf[t] = tf.get(t, 0) + 1
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                f = tf[t]
                s += self.idf.get(t, 0.0) * f * (self.k1 + 1) / (
                    f + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg))
            out.append((s, i))
        out.sort(key=lambda x: (-x[0], x[1]))
        return out


def load_chunks():
    return [json.loads(l) for l in open(CHUNKS, encoding="utf-8") if l.strip()]


def retrieve(query: str, top_k: int = 5):
    """返回 top_k 证据块，附带归一化得分（0~1，以最高分为 1）。"""
    chunks = load_chunks()
    bm = BM25(chunks)
    ranked = bm.score(query)[:top_k]
    if not ranked or ranked[0][0] <= 0:
        return []
    top = ranked[0][0]
    return [{"rank": r + 1, "id": chunks[i]["id"], "article": chunks[i]["article"],
             "chapter": chunks[i]["chapter"], "section": chunks[i]["section"],
             "text": chunks[i]["text"], "score": round(s / top, 4), "raw": round(s, 4)}
            for r, (s, i) in enumerate(ranked)]


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "请假三天以上由谁批准"
    for e in retrieve(q, 3):
        print(e["rank"], e["id"], e["score"], e["text"][:60])
