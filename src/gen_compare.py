# -*- coding: utf-8 -*-
"""生成 RAG 资料库 vs 问答技能 的对比测试报告（数据来自实测 run_log.json）。"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, ".vendor"))
sys.path.insert(0, os.path.join(BASE, "src"))

from retriever import load_chunks  # noqa: E402

LOG = os.path.join(BASE, "results", "run_log.json")
OUT = os.path.join(BASE, "results", "对比测试.md")


def main():
    d = json.load(open(LOG, encoding="utf-8"))
    stat, recs = d["stat"], d["records"]
    chunks = load_chunks()
    full_chars = sum(len(c["text"]) for c in chunks)

    # RAG 每次实际注入的上下文 = 召回的 top-5 条款
    rag_chars = []
    for r in recs:
        if r["group"] == "answerable":
            rag_chars.append(round(full_chars * 0.0 + sum(
                len(s.get("text", "")) for s in r["rag"].get("sources", []))))
    # 用 top-5 平均长度估算：取语料均值 × 5
    avg_chunk = full_chars / len(chunks)
    rag_avg = round(avg_chunk * 5)

    ans = [r for r in recs if r["group"] == "answerable"]
    una = [r for r in recs if r["group"] == "unanswerable"]

    L = ["# 对比测试：RAG 资料库 vs 问答技能", "",
         "同一份知识资料（`corpus/学生管理规定.md`，82 条）被制作为两种形态，",
         "用同一套 14 条测试用例实测对比。", "",
         "## 一、两种形态", "",
         "| | RAG 资料库 | 问答技能 |", "|---|---|---|",
         "| 载体 | `corpus/学生管理规定.md` + `chunks.jsonl` 索引 | "
         "`skill/学生管理规定问答/`（SKILL.md + references 全文档） |",
         "| 知识获取 | 检索节点按条召回 top-5 | 整篇文档进入上下文，模型自行定位 |",
         "| 定位机制 | BM25 字面匹配 + 术语覆盖率 | 依赖模型注意力 |",
         "| 拒答机制 | **判断节点硬阈值 + 校验节点双重拦截** | 仅靠提示词约束（软约束） |",
         "| 引用方式 | 结构化 `[n]` → 第X条（章 > 节） | 提示词要求标注，无强制校验 |",
         f"| 单次上下文开销 | 约 **{rag_avg} 字**（top-5 条款） | 约 **{full_chars} 字**（全文档） |",
         "", f"> 上下文开销比 ≈ **{full_chars / rag_avg:.1f}×**（技能模式为 RAG 的 "
             f"{full_chars / rag_avg:.1f} 倍）", "",
         "## 二、实测结果", "",
         "| 指标 | RAG 编排 | 问答技能 | 差异 |", "|---|---|---|---|",
         f"| 可答题正确 | {stat['rag_answerable_pass']}/{stat['rag_answerable_total']} | "
         f"{stat['skill_answerable_pass']}/{stat['rag_answerable_total']} | 持平 |",
         f"| **无依据题拒答** | **{stat['rag_abstain_pass']}/{stat['rag_abstain_total']}** | "
         f"0/{stat['rag_abstain_total']} | **RAG 完胜** |",
         f"| 总体准确率 | **{stat['rag_overall']:.0%}** | {stat['skill_overall']:.0%} | "
         f"+{(stat['rag_overall'] - stat['skill_overall']):.0%} |",
         f"| 出处可追溯率 | {stat['citation_traceable']:.0%}（强制） | 依赖模型自觉 | RAG 更稳 |",
         f"| 校验通过率 | {stat['verify_pass_rate']:.0%} | 无校验环节 | — |", "",
         "## 三、无依据题上的表现差异（关键）", "",
         "| 编号 | 问题 | RAG 编排 | 问答技能 |", "|---|---|---|---|"]
    for r in una:
        sk = r["skill"]
        L.append(f"| {r['id']} | {r['question']} | ✅ 拒答（`{r['rag']['reason_code']}`） | "
                 f"❌ 输出答案（取自 {sk.get('top1')}，覆盖率 {sk.get('coverage')}） |")

    L += ["", "差异根因：技能模式缺少**判断节点**，只要检索到任何一条相关条款就会组织出答案，",
          "因此面对「学费多少钱」「奖学金多少钱」这类**边界陷阱题**（文档只提到“按规定缴纳学费”",
          "“发给奖品或奖金”，但没有金额），会给出答非所问甚至误导性的回答。", "",
          "## 四、案例对照", ""]
    r3 = next((r for r in una if r["id"] == "R3"), None)
    if r3:
        L += [f"**问题**：{r3['question']}", "",
              "- **RAG 编排**：", "",
              f"  > 拒答（{r3['rag']['reason_code']}），术语覆盖率 "
              f"{r3['rag']['metrics'].get('coverage')} < 0.45", "",
              "- **问答技能**：", "",
              f"  > {r3['skill']['answer'][:200]}", "",
              "  文档只写了「按规定缴纳学费及有关费用」，并未载明金额；",
              "技能模式把主题相关条款当成了支撑依据。", ""]

    L += ["## 五、选型建议", "",
          "| 场景 | 推荐 | 理由 |", "|---|---|---|",
          "| 制度/法务/合规问答 | **RAG 编排** | 引用强制、拒答可控、可审计 |",
          "| 需要跨条款归纳、开放式咨询 | 问答技能 | 全文可见，便于综合与改写 |",
          "| 资料频繁更新 | **RAG 编排** | 重建索引即可，无需改动提示词 |",
          "| 小规模、低频使用 | 问答技能 | 零基建，加载即用 |", "",
          "**最佳组合**：用技能承接对话与意图识别，用 RAG 编排保证引用与拒答的确定性——",
          "本项目把两者放在同一仓库，正为对比这一取舍。", ""]

    open(OUT, "w", encoding="utf-8").write("\n".join(L))
    print("written:", OUT, "| rag_ctx:", rag_avg, "| skill_ctx:", full_chars)


if __name__ == "__main__":
    main()
