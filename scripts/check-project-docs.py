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
PROJECT_MANUAL = "docs/项目说明书.md"
CHANGELOG = "docs/CHANGELOG.md"
SYNC_DOCS = {PROJECT_MANUAL, CHANGELOG}


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "core.quotepath=false", *args],
        text=True,
        encoding="utf-8",
        errors="strict",
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
    project_changes = changed - SYNC_DOCS
    if not project_changes:
        print("Only synchronization documents changed; documentation sync check passed.")
        return 0

    missing = []
    if PROJECT_MANUAL not in changed:
        missing.append(PROJECT_MANUAL)
    if any(affects_project(path) for path in project_changes) and CHANGELOG not in changed:
        missing.append(CHANGELOG)
    if missing:
        print("Project changed without synchronized documentation:", file=sys.stderr)
        for path in missing:
            print(f"- {path}", file=sys.stderr)
        return 1

    print("Project manual is synchronized; behavior changes also include the changelog.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
