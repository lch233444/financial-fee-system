from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.statement_parser import parse_empf_statement  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="在不写入数据库的情况下评估本地账单OCR/文本层解析结果。"
    )
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--include-raw-text",
        action="store_true",
        help="在评估输出中包含OCR原文，便于调试；原文可能含客户资料。",
    )
    args = parser.parse_args()

    results: list[dict] = []
    for source in args.files:
        started = time.perf_counter()
        parsed = parse_empf_statement(source.resolve())
        result = {
            "file": str(source.resolve()),
            "elapsed_seconds": round(time.perf_counter() - started, 2),
            "extracted": parsed.extracted_dict(),
            "confidence": parsed.confidence,
            "warnings": parsed.warnings,
            "raw_text_length": len(parsed.raw_text),
        }
        if args.include_raw_text:
            result["raw_text"] = parsed.raw_text
        results.append(result)

    payload = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
