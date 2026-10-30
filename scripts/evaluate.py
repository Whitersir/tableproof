"""有人工预设答案的合成数据回归评测；不评估真实模型能力。"""
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.reconcile import MatchConfig, reconcile
from app.tables import parse_csv


def main():
    cases = json.loads((ROOT / "evaluations" / "cases.json").read_text(encoding="utf-8"))
    records = []
    for case in cases:
        cfg = MatchConfig.model_validate({"registration_keys": ["id"], "checkin_keys": ["id"], **case.get("config", {})})
        result = reconcile(parse_csv(case["registration"].encode(), "报名.csv"), parse_csv(case["checkin"].encode(), "签到.csv"), cfg)
        differences = {k: {"expected": v, "actual": result["totals"].get(k)} for k, v in case["expected"].items() if result["totals"].get(k) != v}
        if "absent" in case:
            actual = sorted(d["key"][0] for d in result["details"] if d["status"] == "未到场")
            if actual != sorted(case["absent"]):
                differences["absent_keys"] = {"expected": case["absent"], "actual": actual}
        missing_issues = set(case.get("issues", [])) - {i["type"] for i in result["issues"]}
        if missing_issues:
            differences["missing_issues"] = sorted(missing_issues)
        records.append({"id": case["id"], "passed": not differences, "differences": differences, "totals": result["totals"]})
    output = {"evaluated_at_utc": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
              "kind": "deterministic_business_regression", "data": "synthetic_with_manual_expected_answers",
              "real_model_evaluated": False, "total": len(records), "passed": sum(r["passed"] for r in records), "cases": records}
    path = ROOT / "evaluations" / "latest.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f'业务评测：{output["passed"]}/{output["total"]} 项符合人工预设答案。真实模型未评测。')
    print(path)
    return 0 if output["passed"] == output["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
