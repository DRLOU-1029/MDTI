#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MDTI 评分器 —— 12 题二选一 -> 四轴十六型。

类型必须由本脚本算出。任何情况下都不要让模型「感觉」用户是哪个类型。
同一组答案必须永远得到同一个结果。
"""

from __future__ import annotations

import argparse
import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_PATH = ROOT / "references" / "questions.json"
TYPES_PATH = ROOT / "references" / "types.json"
AXIS_ORDER = ("social", "novelty", "basis", "restraint")


def _fail(msg: str) -> NoReturn:
    print(f"[mdti] {msg}", file=sys.stderr)
    raise SystemExit(2)


@lru_cache(maxsize=8)
def _load(path: Path) -> dict:
    if not path.exists():
        _fail(f"找不到 {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"{path.name} 不是合法 JSON：{exc}")


def _strength(best: int, total: int) -> str:
    """每轴 3 题，胜负必为 3:0 或 2:1，只有两档。"""
    return "强烈" if best == total else "偏向"


def score(answers: list[str]) -> dict:
    """answers 为按题目顺序排列的 A/B 列表，返回结果字典。"""
    qdata = _load(QUESTIONS_PATH)
    types = _load(TYPES_PATH)
    questions = qdata["questions"]
    axes = qdata["axes"]

    if len(answers) != len(questions):
        _fail(f"需要 {len(questions)} 个答案，收到 {len(answers)} 个")

    counts: dict[str, dict[str, int]] = {axis: {} for axis in AXIS_ORDER}
    normalized: list[str] = []

    for number, (raw, question) in enumerate(zip(answers, questions), start=1):
        key = str(raw).strip().upper()
        option = next((o for o in question["options"] if o["key"].upper() == key), None)
        if option is None:
            _fail(f"第 {number} 题只能选 A / B，收到 {raw!r}")
        bucket = counts[question["axis"]]
        bucket[option["pole"]] = bucket.get(option["pole"], 0) + 1
        normalized.append(key)

    code_chars: list[str] = []
    ascii_chars: list[str] = []
    report: list[dict] = []

    for axis in AXIS_ORDER:
        poles = list(axes[axis]["poles"])  # 有序：平票时靠前的一极胜出
        bucket = counts[axis]
        total = sum(bucket.values())
        if total == 0:
            _fail(f"轴 {axis} 没有对应题目")
        winner = max(poles, key=lambda p: (bucket.get(p, 0), -poles.index(p)))
        best = bucket.get(winner, 0)
        code_chars.append(winner)
        ascii_chars.append(axes[axis]["poles"][winner])
        report.append({
            "axis": axis,
            "label": axes[axis]["label"],
            "winner": winner,
            "detail": " / ".join(f"{p} {bucket.get(p, 0)}" for p in poles),
            "percent": round(best * 100 / total),
            "strength": _strength(best, total),
        })

    code = "".join(code_chars)
    card = types.get(code)
    if card is None:
        _fail(f"类型 {code} 未收录，请检查 types.json（应有 16 型）")

    return {
        "code": code,
        "ascii": "".join(ascii_chars),
        "name": card["name"],
        "tagline": card["tagline"],
        "action": card["action"],
        "taboo": card["taboo"],
        "axes": report,
        "answers": normalized,
    }


def render(result: dict) -> str:
    bars = " ｜ ".join(f"{a['winner']} {a['percent']}%" for a in result["axes"])
    return "\n".join([
        "🎭 MDTI · 麦门点餐人格",
        "",
        f"  {result['code']} · {result['ascii']}",
        f"  「{result['name']}」",
        "",
        f"  > {result['tagline']}",
        "",
        f"  招牌　{result['action']}",
        f"  雷区　{result['taboo']}",
        "",
        f"  {bars}",
    ])


def _parse_answers(raw: str) -> list[str]:
    cleaned = raw.replace(" ", "")
    for ch in (",", "，", "、", "/", "|"):
        cleaned = cleaned.replace(ch, "")
    return list(cleaned)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="MDTI 麦门点餐人格 · 确定性评分器")
    parser.add_argument("--answers", help="答案串，如 ABABABABABAB 或 A,B,A,...")
    parser.add_argument("--interactive", "-i", action="store_true", help="逐题作答")
    parser.add_argument("--json", action="store_true", help="输出 JSON（供 agent 解析）")
    parser.add_argument("--list", action="store_true", help="打印题库")
    args = parser.parse_args(argv)

    qdata = _load(QUESTIONS_PATH)

    if args.list:
        for q in qdata["questions"]:
            print(f"{q['id']:>2}. {q['text']}")
            for o in q["options"]:
                print(f"    {o['key']}. {o['text']}")
        return 0

    if args.interactive:
        answers: list[str] = []
        print(qdata["title"])
        print("每题只选 A 或 B，直接回车默认为 A。\n")
        for q in qdata["questions"]:
            print(f"{q['id']}. {q['text']}")
            for o in q["options"]:
                print(f"   {o['key']}. {o['text']}")
            while True:
                picked = input("   > ").strip().upper() or "A"
                if picked in ("A", "B"):
                    answers.append(picked)
                    break
                print("   只能选 A 或 B。")
            print()
    elif args.answers:
        answers = _parse_answers(args.answers)
    else:
        parser.print_help()
        return 1

    result = score(answers)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
