#!/usr/bin/env python3
"""Run composer.compose() over dataset/test_pairs.json -> submission.jsonl"""
import json
from pathlib import Path
import composer

ROOT = Path(__file__).parent
DATASET = ROOT / "dataset"


def load(kind, cid):
    if cid is None:
        return None
    p = DATASET / kind / f"{cid}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def main():
    pairs = json.loads((DATASET / "test_pairs.json").read_text())["pairs"]
    out_lines = []
    for pair in pairs:
        trigger = load("triggers", pair["trigger_id"])
        merchant = load("merchants", pair["merchant_id"])
        customer = load("customers", pair.get("customer_id"))
        category = load("categories", merchant["category_slug"]) if merchant else None

        composed = composer.compose(category, merchant, trigger, customer)
        line = {
            "test_id": pair["test_id"],
            "body": composed["body"],
            "cta": composed["cta"],
            "send_as": composed["send_as"],
            "suppression_key": composed["suppression_key"],
            "rationale": composed["rationale"],
        }
        out_lines.append(line)
        print(f"{pair['test_id']} [{trigger['kind']}] {merchant['identity']['name']}")
        print(f"   -> {composed['body']}\n")

    with open(ROOT / "submission.jsonl", "w") as f:
        for line in out_lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    print(f"\nWrote {len(out_lines)} lines to submission.jsonl")


if __name__ == "__main__":
    main()
