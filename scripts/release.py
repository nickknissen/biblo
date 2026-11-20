"""Release a new version: bump, commit, push and create the GitHub release.

    uv run python scripts/release.py 0.2.0

Publishing the GitHub release runs .github/workflows/publish.yml, which tests, builds and
uploads to PyPI with trusted publishing.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = re.compile(r"\d+\.\d+\.\d+")


class ReleaseError(Exception):
    pass


def run(*cmd: str) -> str:
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise ReleaseError(f"{' '.join(cmd)} failed:\n{result.stderr}")
    return result.stdout.strip()


def parse(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def release(version: str, *, dry_run: bool) -> None:
    if not VERSION.fullmatch(version):
        raise ReleaseError(f"{version} is not an X.Y.Z version")
    current = run("uv", "version", "--short")
    if parse(version) < parse(current) or (
        parse(version) == parse(current) and run("git", "tag", "--list", f"v{version}")
    ):
        raise ReleaseError(f"{version} is not newer than {current}")
    if run("git", "branch", "--show-current") != "main":
        raise ReleaseError("Release from the main branch")
    if run("git", "status", "--porcelain"):
        raise ReleaseError("Commit or stash your changes first")
    run("git", "pull", "--ff-only")
    if dry_run:
        print(f"Would release {current} -> {version}")
        return

    if version != current:
        run("uv", "version", version)  # updates pyproject.toml and uv.lock
        run("git", "add", "pyproject.toml", "uv.lock")
        run("git", "commit", "-m", f"Release v{version}")
        run("git", "push", "origin", "main")
    run("gh", "release", "create", f"v{version}", "--title", f"v{version}", "--generate-notes")
    print(f"Released v{version}. Follow the upload with: gh run watch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", metavar="X.Y.Z")
    parser.add_argument("--dry-run", action="store_true", help="only check, change nothing")
    args = parser.parse_args()
    try:
        release(args.version, dry_run=args.dry_run)
    except ReleaseError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
