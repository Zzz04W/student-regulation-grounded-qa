# -*- coding: utf-8 -*-
"""命令行问答入口：python src/ask.py "学生请假3天以上由谁批准？"

输出答案为 JSON，包含答案原文、条款级出处、判断指标与校验报告。
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, ".vendor"))
sys.path.insert(0, os.path.join(BASE, "src"))

from engine import run  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    result = run(sys.argv[1])
    out = sys.stdout
    out.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
