"""从原始检查文件汇总公开记录，不访问任何模型。"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]


def main():
    suite = ElementTree.parse(ROOT / "test-results/pytest.xml").getroot().find("testsuite")
    unit = {k: int(suite.attrib[k]) for k in ("tests", "failures", "errors", "skipped")}
    unit["seconds"] = float(suite.attrib["time"])
    business = json.loads((ROOT / "evaluations/latest.json").read_text(encoding="utf-8"))
    browser = json.loads((ROOT / "test-results/browser-check.json").read_text(encoding="utf-8"))
    mock = json.loads((ROOT / "test-results/browser-agent-check.json").read_text(encoding="utf-8"))
    sources = ["test-results/pytest.xml", "evaluations/latest.json", "test-results/browser-check.json", "test-results/browser-agent-check.json"]
    result = {"recorded_at_utc": datetime.now(timezone.utc).isoformat(), "scope": "local_checks",
              "unit_tests": unit, "business_cases": {"passed": business["passed"], "total": business["total"]},
              "browser": {"passed": browser["passed"], "check_groups": len(browser["checks"])},
              "mock_http_integration": {"passed": mock["passed"], "check_groups": len(mock["checks"])},
              "real_model_tested": False,
              "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources}}
    (ROOT / "evaluations/checks.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "source_sha256"}, ensure_ascii=False, indent=2))
    return int(bool(unit["failures"] or unit["errors"] or business["passed"] != business["total"] or not browser["passed"] or not mock["passed"]))


if __name__ == "__main__":
    sys.exit(main())
