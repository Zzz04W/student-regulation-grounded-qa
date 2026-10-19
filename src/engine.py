# -*- coding: utf-8 -*-
"""编排引擎：加载 orchestration/workflow.yaml 并按节点/边执行四环节流程。

节点顺序：retrieve → judge →(ANSWER) generate → verify →(pass) output
                              ↘(ABSTAIN)      ↘(fail)   abstain
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, ".vendor"))
sys.path.insert(0, os.path.join(BASE, "src"))

import yaml  # noqa: E402
from retriever import retrieve, tokenize, load_chunks  # noqa: E402

WF = os.path.join(BASE, "orchestration", "workflow.yaml")
CN_DIGITS = "一二三四五六七八九十百"


def load_workflow():
    return yaml.safe_load(open(WF, encoding="utf-8").read())


# ---------------- 工具 ----------------
def split_sentences(text: str) -> list:
    parts = re.split(r"(?<=[。；;])", text)
    return [p.strip() for p in parts if p.strip()]


def gram_set(text: str) -> set:
    return set(tokenize(text))


def coverage(query: str, doc: str) -> float:
    """查询术语在文档中的加权覆盖率（bi-gram 权重 2，uni-gram 权重 1）。"""
    qt, dt = set(tokenize(query)), gram_set(doc)
    w = h = 0.0
    for t in qt:
        w += 2 if len(t) > 1 else 1
        if t in dt:
            h += 2 if len(t) > 1 else 1
    return round(h / w, 4) if w else 0.0


def sentence_score(query: str, sent: str) -> float:
    qs, ss = gram_set(query), gram_set(sent)
    if not qs:
        return 0.0
    return len(qs & ss) / len(qs)


def extract_numbers(text: str) -> set:
    nums = set(re.findall(r"\d+(?:\.\d+)?%?", text))
    nums |= set(re.findall(r"[%s]+" % CN_DIGITS, text))
    return {n for n in nums if len(n) > 0}


# ---------------- 节点实现 ----------------
def node_retrieve(ctx, cfg):
    top_k = cfg["params"]["top_k"]
    ev = retrieve(ctx["question"], top_k=top_k)
    ctx["evidence"] = ev
    ctx.setdefault("trace", []).append({
        "node": "retrieve", "top_k": top_k, "hits": len(ev),
        "top1": ev[0]["id"] if ev else None})
    return ctx


def node_judge(ctx, cfg):
    p = cfg["params"]
    ev = ctx["evidence"]
    if not ev:
        ctx["decision"], ctx["abstain_reason"] = "ABSTAIN", "NO_EVIDENCE"
        ctx["metrics"] = {"score_raw": 0.0, "coverage": 0.0}
        return ctx
    top = ev[0]
    cov = coverage(ctx["question"], top["text"])
    raw = top["raw"]
    ctx["metrics"] = {"score_raw": raw, "coverage": cov, "top1_id": top["id"]}
    if raw >= p["min_score"] and cov >= p["min_coverage"]:
        ctx["decision"] = "ANSWER"
    elif cov < p["min_coverage"] and raw >= 8.0:
        ctx["decision"], ctx["abstain_reason"] = "ABSTAIN", "PARTIAL_EVIDENCE"
    else:
        ctx["decision"], ctx["abstain_reason"] = "ABSTAIN", "NO_EVIDENCE"
    ctx["trace"].append({"node": "judge", "decision": ctx["decision"],
                         "reason": ctx.get("abstain_reason"), **ctx["metrics"]})
    return ctx


def node_generate(ctx, cfg):
    """ grounded 生成：仅从证据原文抽取句子，句尾附加 [n] 引用。
    若设置 OPENAI_API_KEY 则调用真实大模型（此处保留接口位）。"""
    q, ev = ctx["question"], ctx["evidence"]
    if ctx.get("force_hallucination"):
        ctx["answer"] = "根据学校规定，图书馆图书逾期每天罚款0.5元，宿舍每晚23:30熄灯。 [1]"
        ctx["citations"] = [1]
        return ctx
    body = re.sub(r"^第\d+条\s*", "", ev[0]["text"]).strip()
    # 条款通常短小且语义完整：默认整条输出（避免断章取义）；
    # 仅当条款超过 300 字时才用连续窗口定位到最相关的片段。
    if len(body) > 300:
        sents = split_sentences(ev[0]["text"])
        scored = sorted(((sentence_score(q, s), i) for i, s in enumerate(sents)),
                        key=lambda x: (-x[0], x[1]))
        best_score, best_i = scored[0]
        lo = hi = best_i
        while lo - 1 >= 0 and sentence_score(q, sents[lo - 1]) >= 0.35 * best_score:
            lo -= 1
        while hi + 1 < len(sents) and sentence_score(q, sents[hi + 1]) >= 0.35 * best_score:
            hi += 1
        if re.search(r"[（(]一[）)]", ev[0]["text"]):
            start = next((i for i, s in enumerate(sents) if re.search(r"[（(]一[）)]", s)), lo)
            lo = min(lo, start)
            hi = len(sents) - 1
        body = "".join(sents[lo:hi + 1])
        body = re.sub(r"^第\d+条\s*", "", body).strip()
    # 去掉行首条号前缀，条号由引用标记承担
    body = re.sub(r"^第\d+条\s*", "", body).strip()
    answer = f"{body} [1]"
    # 次条证据得分接近时并入作为补充依据
    if len(ev) > 1 and ev[1]["score"] >= 0.85:
        s2 = re.sub(r"^第\d+条\s*", "", split_sentences(ev[1]["text"])[0]).strip()
        answer += f" 另据{ev[1]['id']}：{s2} [2]"
        ctx["citations"] = [1, 2]
    else:
        ctx["citations"] = [1]
    ctx["answer"] = answer
    ctx["trace"].append({"node": "generate", "citations": ctx["citations"],
                         "answer_len": len(answer)})
    return ctx


def node_verify(ctx, cfg):
    p = cfg["params"]
    ev, ans = ctx["evidence"], ctx["answer"]
    cited = {int(n) for n in re.findall(r"\[(\d+)\]", ans)}
    checks = []

    # 1) 引用完整性
    ok_cite = bool(cited) and all(1 <= n <= len(ev) for n in cited)
    checks.append({"id": "citation_validity", "pass": ok_cite,
                   "detail": f"引用编号 {sorted(cited)} / 证据范围 1..{len(ev)}"})

    # 2) 事实落地率（剥离引用标记后再比对，避免 [n] 干扰）
    plain = re.sub(r"\[\d+\]", "", ans)
    ref_text = " ".join(ev[n - 1]["text"] for n in cited if 1 <= n <= len(ev)) or ""
    g = coverage(plain, ref_text) if ref_text else 0.0
    ok_g = g >= p["verify_min_groundedness"]
    checks.append({"id": "groundedness", "pass": ok_g,
                   "detail": f"落地率 {g} >= {p['verify_min_groundedness']}"})

    # 3) 数字一致性（同样基于剥离引用后的正文）
    if p.get("verify_require_numeric_check", True):
        an, rn = extract_numbers(plain), extract_numbers(ref_text)
        bad = [n for n in an if n not in rn]
        ok_n = not bad
        checks.append({"id": "numeric_consistency", "pass": ok_n,
                       "detail": f"答案数字 {sorted(an)}；未落地 {bad or '无'}"})
    else:
        checks.append({"id": "numeric_consistency", "pass": True, "detail": "已跳过"})

    passed = all(c["pass"] for c in checks)
    ctx["verify"] = {"verified": passed, "checks": checks, "groundedness": g}
    if not passed:
        ctx["abstain_reason"] = "VERIFY_FAILED"
    ctx["trace"].append({"node": "verify", "verified": passed,
                         "failed": [c["id"] for c in checks if not c["pass"]]})
    return ctx


def node_abstain(ctx, cfg):
    ev = ctx["evidence"]
    top = ev[0] if ev else None
    reason_map = {
        "NO_EVIDENCE": "资料库中不存在与本问题相关的条款",
        "PARTIAL_EVIDENCE": "存在相关条款，但不足以支撑本问题的具体结论",
        "VERIFY_FAILED": "生成结果未能通过出处校验，已拦截",
    }
    r = ctx.get("abstain_reason", "NO_EVIDENCE")
    m = ctx.get("metrics", {})
    ctx["final"] = {
        "type": "ABSTAIN",
        "answer": "未在《大连财经学院学生管理规定》中检索到可支撑本问题的依据，暂不作答。",
        "reason_code": r,
        "reason": reason_map.get(r, r),
        "evidence_hint": (f"{top['id']}（{top['chapter']} / {top['section']}）"
                          f"「{top['text'][:40]}…」" if top else "无"),
        "metrics": m,
        "verify": ctx.get("verify"),
        "advice": "该事项不在本规定覆盖范围内，建议向学生处或学校办公室核实。",
    }
    return ctx


def node_output(ctx, cfg):
    ev, cited = ctx["evidence"], ctx.get("citations", [])
    sources = []
    for n in cited:
        if 1 <= n <= len(ev):
            e = ev[n - 1]
            sources.append({"ref": n, "id": e["id"],
                            "path": f"{e['chapter']} > {e['section']}" if e["section"] else e["chapter"],
                            "score": e["score"], "text": e["text"]})
    ctx["final"] = {"type": "ANSWER", "answer": ctx["answer"], "sources": sources,
                    "verify": ctx["verify"], "metrics": ctx.get("metrics", {})}
    return ctx


NODES = {"retrieve": node_retrieve, "judge": node_judge, "generate": node_generate,
         "verify": node_verify, "abstain": node_abstain, "output": node_output}


def run(question: str, cfg=None, force_hallucination=False) -> dict:
    cfg = cfg or load_workflow()
    ctx = {"question": question, "trace": [], "force_hallucination": force_hallucination}
    node_retrieve(ctx, cfg)
    node_judge(ctx, cfg)
    if ctx["decision"] == "ANSWER":
        node_generate(ctx, cfg)
        node_verify(ctx, cfg)
        node_output(ctx, cfg) if ctx["verify"]["verified"] else node_abstain(ctx, cfg)
    else:
        node_abstain(ctx, cfg)
    ctx["final"]["trace"] = ctx["trace"]
    return ctx["final"]


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "请假三天以上由谁批准"
    r = run(q)
    print("Q:", q)
    print(json.dumps(r, ensure_ascii=False, indent=2) if (json := __import__("json")) else r)
