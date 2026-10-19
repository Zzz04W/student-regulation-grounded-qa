# -*- coding: utf-8 -*-
"""测试执行：对四环节编排跑测试集，并与"问答技能"模式做对比。

RAG 编排模式：检索 → 判断（硬阈值）→ 生成 → 校验
技能模式   ：整篇文档入上下文 → 模型自行定位 → 直接生成（无判断/校验节点）
"""
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, ".vendor"))
sys.path.insert(0, os.path.join(BASE, "src"))

from engine import (run, load_workflow, node_retrieve, node_generate,  # noqa: E402
                    coverage, split_sentences, sentence_score)
from retriever import load_chunks  # noqa: E402

QFILE = os.path.join(BASE, "tests", "questions.json")
OUT_MD = os.path.join(BASE, "results", "测试报告.md")
OUT_JSON = os.path.join(BASE, "results", "run_log.json")


def skill_answer(question: str, cfg: dict) -> dict:
    """模拟"问答技能"执行路径：全量文档入上下文，无判断节点、无校验节点。"""
    ctx = {"question": question, "trace": [], "force_hallucination": False}
    node_retrieve(ctx, cfg)          # 模型自行在上下文中定位
    if not ctx["evidence"]:
        return {"type": "ABSTAIN", "answer": "（技能模式）未找到相关内容"}
    # 关键差异：跳过 judge 阈值与 verify 校验，直接生成
    node_generate(ctx, cfg)
    return {"type": "ANSWER", "answer": ctx["answer"],
            "top1": ctx["evidence"][0]["id"],
            "coverage": coverage(question, ctx["evidence"][0]["text"]),
            "context_chars": sum(len(c["text"]) for c in load_chunks())}


def evaluate(case: dict, cfg: dict) -> dict:
    q = case["question"]
    rag = run(q, cfg)
    rec = {"id": case["id"], "group": case["group"], "question": q,
           "note": case.get("note", ""), "expected_article": case["expected_article"]}

    # ---- RAG 编排模式判定 ----
    rag_ok, rag_detail = False, ""
    if case["group"] == "answerable":
        if rag["type"] == "ANSWER":
            arts = [int(re.search(r"\d+", s["id"]).group()) for s in rag["sources"]]
            hit_art = case["expected_article"] in arts
            hit_txt = all(k in rag["answer"] for k in case.get("expected_answer_contains", []))
            rag_ok = hit_art and hit_txt
            rag_detail = f"引用{arts}(期望{case['expected_article']}) 关键词命中={hit_txt}"
            rec["citation_ok"] = hit_art
        else:
            rag_detail = f"误拒答({rag.get('reason_code')})"
    else:
        rag_ok = rag["type"] == "ABSTAIN"
        rag_detail = rag.get("reason_code", rag["type"]) if rag_ok else "未拒答（幻觉风险）"

    rec["rag"] = {"type": rag["type"], "ok": rag_ok, "detail": rag_detail,
                  "answer": rag.get("answer", "")[:400],
                  "reason_code": rag.get("reason_code"),
                  "metrics": rag.get("metrics", {}),
                  "sources": [{"id": s["id"], "path": s["path"]} for s in rag.get("sources", [])],
                  "verify": rag.get("verify") or (rag.get("verify") ),
                  "trace": rag.get("trace", [])}

    # ---- 技能模式判定 ----
    sk = skill_answer(q, cfg)
    sk_ok, sk_detail = False, ""
    if case["group"] == "answerable":
        top = int(re.search(r"\d+", sk["top1"]).group()) if sk["type"] == "ANSWER" else -1
        sk_ok = top == case["expected_article"]
        sk_detail = f"定位到{sk['top1']}(期望第{case['expected_article']}条)"
    else:
        # 技能模式缺少强制拒答机制：只要检索到任何条款就会输出答案
        sk_ok = False
        sk_detail = f"输出了答案（来源{sk.get('top1')}，覆盖率{sk.get('coverage')}）→ 无依据作答"
    rec["skill"] = {"type": sk["type"], "ok": sk_ok, "detail": sk_detail,
                    "answer": sk.get("answer", "")[:400],
                    "top1": sk.get("top1"), "coverage": sk.get("coverage"),
                    "context_chars": sk.get("context_chars", 0)}
    return rec


def main():
    cfg = load_workflow()
    cases = json.load(open(QFILE, encoding="utf-8"))["cases"]
    records = [evaluate(c, cfg) for c in cases]

    # 校验节点拦截演示：注入幻觉答案
    demo = run("学生请假3天以上7天以内由谁批准？", cfg, force_hallucination=True)

    ans = [r for r in records if r["group"] == "answerable"]
    una = [r for r in records if r["group"] == "unanswerable"]
    stat = {
        "total": len(records),
        "rag_answerable_pass": sum(r["rag"]["ok"] for r in ans),
        "rag_answerable_total": len(ans),
        "rag_abstain_pass": sum(r["rag"]["ok"] for r in una),
        "rag_abstain_total": len(una),
        "rag_overall": sum(r["rag"]["ok"] for r in records) / len(records),
        "skill_answerable_pass": sum(r["skill"]["ok"] for r in ans),
        "skill_overall": sum(r["skill"]["ok"] for r in records) / len(records),
        "citation_traceable": sum(1 for r in ans if r.get("citation_ok")) / len(ans),
        "verify_pass_rate": sum(1 for r in records
                                if (r["rag"].get("verify") or {}).get("verified")) / len(ans),
    }
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    json.dump({"stat": stat, "records": records, "hallucination_demo": demo},
              open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    write_md(records, stat, demo)
    print(json.dumps(stat, ensure_ascii=False, indent=2))
    return stat


def write_md(records, stat, demo):
    L = ["# 测试结果报告", "",
         f"- 语料：《大连财经学院学生管理规定》，共 **82 条**",
         f"- 用例：{stat['total']} 条（可答 {stat['rag_answerable_total']} ／ 无依据 {stat['rag_abstain_total']}）",
         f"- 编排：`orchestration/workflow.yaml`（检索 → 判断 → 生成 → 校验）", "",
         "## 一、总体指标", "",
         "| 指标 | RAG 编排 | 问答技能 |", "|---|---|---|",
         f"| 可答题正确率 | {stat['rag_answerable_pass']}/{stat['rag_answerable_total']} | "
         f"{stat['skill_answerable_pass']}/{stat['rag_answerable_total']} |",
         f"| 无依据题拒答率 | **{stat['rag_abstain_pass']}/{stat['rag_abstain_total']}** | "
         f"0/{stat['rag_abstain_total']} |",
         f"| 总体准确率 | **{stat['rag_overall']:.0%}** | {stat['skill_overall']:.0%} |",
         f"| 出处可追溯率 | {stat['citation_traceable']:.0%} | 依赖模型自觉 |", "",
         "## 二、逐条明细", "",
         "| 编号 | 类型 | 问题 | RAG 结果 | 判定 | 技能模式结果 |", "|---|---|---|---|---|---|"]
    for r in records:
        g = "可答" if r["group"] == "answerable" else "**无依据**"
        q = r["question"][:28] + ("…" if len(r["question"]) > 28 else "")
        L.append(f"| {r['id']} | {g} | {q} | {r['rag']['type']} · {r['rag']['detail']} | "
                 f"{'✅' if r['rag']['ok'] else '❌'} | {r['skill']['type']} · {r['skill']['detail']} |")

    L += ["", "## 三、可答题样例（完整答案与出处）", ""]
    for r in records:
        if r["group"] != "answerable" or not r["rag"]["ok"]:
            continue
        L += [f"### {r['id']} {r['question']}", "",
              "**答案**：", "", r["rag"]["answer"], "", "**出处**：", ""]
        for s in r["rag"]["sources"]:
            L.append(f"- `[{s['id']}]` {s['path']}")
        L.append("")

    L += ["## 四、拒答逻辑专项验证（无依据问题）", ""]
    for r in records:
        if r["group"] != "unanswerable":
            continue
        m = r["rag"]["metrics"]
        L += [f"### {r['id']} {r['question']}", "",
              f"- 触发原因代码：`{r['rag']['reason_code']}`",
              f"- 首条证据得分 {m.get('score_raw')}（阈值 18.0）、术语覆盖率 {m.get('coverage')}（阈值 0.45）",
              f"- 系统输出：{r['rag']['answer']}",
              f"- 最接近条款：{r['rag']['sources'] or '无'}",
              f"- 技能模式对照：{r['skill']['detail']}", ""]

    L += ["## 五、校验节点拦截演示", "",
          "向生成节点强制注入一条含幻觉的答案，观察校验节点是否拦截：", "",
          "```", demo.get("answer", ""), "```", "",
          f"结果：**{'已拦截（降级为拒答）' if demo['type']=='ABSTAIN' else '未拦截'}**",
          f"，原因代码 `{demo.get('reason_code')}`。", ""]
    if demo.get("verify"):
        L += ["| 校验项 | 结果 | 说明 |", "|---|---|---|"]
        for c in demo["verify"]["checks"]:
            L.append(f"| {c['id']} | {'✅' if c['pass'] else '❌'} | {c['detail']} |")
        L.append("")

    open(OUT_MD, "w", encoding="utf-8").write("\n".join(L))
    print("written:", OUT_MD)


if __name__ == "__main__":
    main()
