from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
from pathlib import Path

import httpx


def _write_progress(output: Path, results: list[dict]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="通过隔离测试实例逐份执行本地OCR和一次Sol交叉核验。"
    )
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8011")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    results: list[dict] = []
    request_headers = {"X-Financial-System-Request": "1"}
    # Recognition intentionally has no retry transport. Each sample gets at
    # most one non-idempotent Sol request in this evaluation run.
    transport = httpx.HTTPTransport(retries=0)
    with httpx.Client(
        base_url=args.base_url,
        timeout=httpx.Timeout(900.0, connect=10.0),
        transport=transport,
    ) as client:
        for source in args.files:
            source = source.resolve()
            result: dict = {"file": str(source)}
            try:
                mime_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
                with source.open("rb") as stream:
                    uploaded = client.post(
                        "/api/statement-imports",
                        files={"file": (source.name, stream, mime_type)},
                        headers=request_headers,
                    )
                uploaded.raise_for_status()
                upload_payload = uploaded.json()
                result["import_id"] = upload_payload["id"]
                result["ocr"] = upload_payload

                started = time.perf_counter()
                recognized = client.post(
                    f"/api/statement-imports/{upload_payload['id']}/ai-recognize",
                    headers=request_headers,
                )
                result["sol_elapsed_seconds"] = round(
                    time.perf_counter() - started, 2
                )
                recognized.raise_for_status()
                result["cross_check"] = recognized.json()
                result["status"] = "completed"
            except (OSError, httpx.HTTPError, KeyError, ValueError) as exc:
                result["status"] = "failed_without_retry"
                result["error"] = str(exc)
            results.append(result)
            _write_progress(args.output, results)
            print(
                json.dumps(
                    {
                        "file": source.name,
                        "status": result["status"],
                        "import_id": result.get("import_id"),
                        "sol_elapsed_seconds": result.get("sol_elapsed_seconds"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    return 0 if all(result["status"] == "completed" for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
