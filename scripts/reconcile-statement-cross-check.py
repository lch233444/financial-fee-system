from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.services.codex_app_server import build_ai_review_result  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="用当前比较规则离线重算已完成的OCR/Luna识别结果，不再次调用模型。"
    )
    parser.add_argument("--ocr", type=Path, required=True)
    parser.add_argument("--luna", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    ocr_rows = json.loads(args.ocr.read_text(encoding="utf-8"))
    luna_rows = json.loads(args.luna.read_text(encoding="utf-8"))
    luna_by_name = {
        Path(row["file"]).name: row for row in luna_rows if isinstance(row, dict)
    }
    results: list[dict] = []
    for ocr_row in ocr_rows:
        name = Path(ocr_row["file"]).name
        luna_row = luna_by_name[name]
        cross_check = luna_row["cross_check"]
        ai_values = cross_check["ai_recognition"]["values"]
        results.append(
            {
                "file": ocr_row["file"],
                "model": cross_check["ai_model"],
                "luna_elapsed_seconds": luna_row.get("luna_elapsed_seconds"),
                "ocr": ocr_row["extracted"],
                "luna": ai_values,
                "review": build_ai_review_result(ocr_row["extracted"], ai_values),
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"files": len(results), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
