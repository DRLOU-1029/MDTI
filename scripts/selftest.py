#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MDTI 自检：不依赖网络，验证「题库 / 档案 / 评分器」三者是否自洽。

用法：python3 scripts/selftest.py
返回码 0 表示全部通过。
"""

from __future__ import annotations

import itertools
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mdti_score as m  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    qdata = m._load(m.QUESTIONS_PATH)
    types = m._load(m.TYPES_PATH)
    questions = qdata["questions"]
    count = len(questions)

    check("题库题量 = 12", count == 12, str(count))
    check("档案型数 = 16", len(types) == 16, str(len(types)))
    check("题目 id 从 1 连续", [q["id"] for q in questions] == list(range(1, count + 1)))

    per_axis = Counter(q["axis"] for q in questions)
    for axis in m.AXIS_ORDER:
        check(f"轴 {axis} 题量 = 3", per_axis.get(axis) == 3, str(per_axis.get(axis)))

    check(
        "每题两个选项、两极互不相同",
        all(len(q["options"]) == 2 and len({o["pole"] for o in q["options"]}) == 2 for q in questions),
    )

    counter: Counter[str] = Counter()
    for combo in itertools.product("AB", repeat=count):
        counter[m.score(list(combo))["code"]] += 1

    expected_total = 2 ** count
    check("穷举组合数 = 4096", sum(counter.values()) == expected_total, str(sum(counter.values())))
    check("十六型全部可达", len(counter) == 16, str(len(counter)))
    check("十六型均分", set(counter.values()) == {expected_total // 16}, str(sorted(set(counter.values()))))
    check("可达类型全部收录在档案里", set(counter) == set(types))

    first = m.score(list("ABABABABABAB"))
    second = m.score(list("ABABABABABAB"))
    check("评分确定性（同答案同结果）", first == second)

    print()
    if failures:
        print(f"FAILED: {len(failures)} 项 -> {failures}")
        return 1
    print(f"ALL PASS（{len(counter)} 型，每型 {expected_total // 16} 种组合）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
