# -*- coding: utf-8 -*-
"""语料构建：将《大连财经学院学生管理规定》清洗为带条号锚点的结构化知识块。

设计要点：
- 以"条"为最小可引用单元（chunk），保证出处引用可追溯到具体条款；
- 每个 chunk 携带 chapter/section/article 三级定位元数据；
- 生成 corpus/学生管理规定.md（可读版）与 corpus/chunks.jsonl（检索索引）。
"""
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(BASE, "corpus", "_raw.txt")
SRC_DOCX = r"C:/Users/EC504/Desktop/学生管理制度.docx"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

CN_NUM = "零一二三四五六七八九十百"


def extract_docx(path: str) -> list:
    """从 docx 中抽取段落文本（不依赖第三方库）。"""
    z = zipfile.ZipFile(path)
    root = ET.fromstring(z.read("word/document.xml"))
    paras = []
    for p in root.iter(W + "p"):
        t = "".join(n.text or "" for n in p.iter(W + "t"))
        paras.append(t.replace("\xa0", " ").strip())
    return paras


def cn_to_int(s: str) -> int:
    """中文数字转阿拉伯数字，仅支持本文档出现的 1-99。"""
    d = {c: i for i, c in enumerate("零一二三四五六七八九")}
    if "十" in s:
        a, _, b = s.partition("十")
        hi = d.get(a, 1) if a else 1
        lo = d.get(b, 0) if b else 0
        return hi * 10 + lo
    return int("".join(str(d[c]) for c in s if c in d) or 0)


def build():
    if not os.path.exists(RAW):
        paras = extract_docx(SRC_DOCX)
        os.makedirs(os.path.dirname(RAW), exist_ok=True)
        with open(RAW, "w", encoding="utf-8") as f:
            f.write("\n".join(paras))
    else:
        paras = open(RAW, encoding="utf-8").read().split("\n")

    chapter, section = "总则", ""
    chunks, buf, cur_article, cur_title = [], [], None, ""

    def flush():
        if cur_article is not None and buf:
            text = "\n".join(buf).strip()
            if text:
                chunks.append({
                    "id": f"第{cur_article}条",
                    "article": cur_article,
                    "chapter": chapter,
                    "section": section,
                    "text": text,
                    "title": cur_title,
                })
        buf.clear()

    for line in paras:
        s = line.strip()
        if not s:
            continue
        if s.startswith("撰稿人") or s.startswith("大连财经学院学生管理规定") or "阅读[" in s:
            continue
        m = re.match(r"^第([一二三四五六七八九十百]+)章\s*(.+)$", s)
        if m:
            flush()
            chapter = s
            section = ""
            cur_article = None
            continue
        m = re.match(r"^第([一二三四五六七八九十]+)节\s*(.+)$", s)
        if m:
            flush()
            section = s
            cur_article = None
            continue
        m = re.match(r"^第([一二三四五六七八九十百]+)条\s*(.*)$", s)
        if m:
            flush()
            cur_article = cn_to_int(m.group(1))
            rest = m.group(2).strip()
            cur_title = rest[:30]
            buf.append(f"第{cur_article}条 {rest}".strip())
            continue
        if cur_article is not None:
            buf.append(s)
    flush()

    # 写入可读版语料
    md = ["# 大连财经学院学生管理规定（知识资料库）", "",
          "> 来源：`学生管理制度.docx`（更新时间 2021/12/16）",
          "> 本文档为 RAG 资料库与问答技能共用的唯一知识来源，"
          "按“条”编号，便于出处引用追溯。", ""]
    last_ch, last_sec = None, None
    for c in chunks:
        if c["chapter"] != last_ch:
            md += [f"\n## {c['chapter']}", ""]
            last_ch, last_sec = c["chapter"], None
        if c["section"] != last_sec:
            md += [f"\n### {c['section']}", ""]
            last_sec = c["section"]
        md += [f"\n**{c['id']}** {c['text'][len(c['id']):].strip()}", ""]
    md_path = os.path.join(BASE, "corpus", "学生管理规定.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    idx_path = os.path.join(BASE, "corpus", "chunks.jsonl")
    with open(idx_path, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"chunks={len(chunks)} md={md_path}")
    print("sample:", json.dumps(chunks[7], ensure_ascii=False)[:200])


if __name__ == "__main__":
    build()
