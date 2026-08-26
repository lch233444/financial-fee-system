from __future__ import annotations

import argparse
import subprocess
import sys


PROJECT_PREFIXES = (
    "backend/app/",
    "backend/alembic/",
    "frontend/src/",
    "packaging/",
    "scripts/",
)
PROJECT_FILES = {
    "backend/requirements.txt",
    "backend/requirements-dev.txt",
    "backend/requirements-dev-lock.txt",
    "frontend/package.json",
    "frontend/pnpm-lock.yaml",
    "frontend/vite.config.ts",
    "新收费计划计算.xlsx",
}
REQUIRED_DOCS = {"docs/项目说明书.md", "docs/CHANGELOG.md"}


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], text=True, encoding="utf-8", errors="strict"
    ).strip()


def changed_files(base: str | None) -> set[str]:
    if not base or set(base) == {"0"}:
        output = git("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "HEAD")
    else:
        output = git("diff", "--name-only", f"{base}...HEAD")
    return {line.replace("\\", "/") for line in output.splitlines() if line.strip()}


def affects_project(path: str) -> bool:
    return path in PROJECT_FILES or path.startswith(PROJECT_PREFIXES)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Require the living project manual and changelog for project changes."
    )
    parser.add_argument("--base", default=None, help="Base commit SHA for comparison")
    args = parser.parse_args()

    changed = changed_files(args.base)
    if not any(affects_project(path) for path in changed):
        print("No project behavior files changed; documentation sync check passed.")
        return 0

    missing = sorted(REQUIRED_DOCS - changed)
    if missing:
        print("Project behavior changed without synchronized documentation:", file=sys.stderr)
        for path in missing:
            print(f"- {path}", file=sys.stderr)
        return 1

    print("Project manual and changelog were updated with project changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

