"""Release a new version: bump, commit, push and create the GitHub release.

    uv run python scripts/release.py 0.2.0

Describe the changes under `## [Unreleased]` in CHANGELOG.md first: that section becomes the
version's section and the GitHub release notes. Publishing the GitHub release runs
.github/workflows/publish.yml, which tests, builds and uploads to PyPI with trusted
publishing.
"""

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
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
    changelog, notes = release_changelog(CHANGELOG.read_text(encoding="utf-8"), version)
    if dry_run:
        print(f"Would release {current} -> {version} with these notes:\n\n{notes}")
        return

    CHANGELOG.write_text(changelog, encoding="utf-8")
    run("uv", "version", version)  # updates pyproject.toml and uv.lock
    run("git", "add", "pyproject.toml", "uv.lock", "CHANGELOG.md")
    run("git", "commit", "-m", f"Release v{version}")
    run("git", "push", "origin", "main")
    run("gh", "release", "create", f"v{version}", "--title", f"v{version}", "--notes", notes)
    print(f"Released v{version}. Follow the upload with: gh run watch")


def release_changelog(text: str, version: str) -> tuple[str, str]:
    """CHANGELOG.md with the Unreleased section turned into `version`, and that section."""
    match = re.search(r"^## \[Unreleased\]\n(.*?)(?=^## \[)", text, re.M | re.S)
    if match is None or not match.group(1).strip():
        raise ReleaseError("Describe the release under '## [Unreleased]' in CHANGELOG.md first")
    notes = match.group(1).strip()
    heading = f"## [Unreleased]\n\n## [{version}] - {date.today().isoformat()}\n\n{notes}\n\n"
    text = text[: match.start()] + heading + text[match.end() :]
    previous = re.search(r"^\[Unreleased\]: .*/compare/(v[\d.]+)\.\.\.HEAD$", text, re.M)
    if previous is None:
        raise ReleaseError("CHANGELOG.md has no [Unreleased] compare link at the bottom")
    repo = previous.group(0).split("/compare/")[0].removeprefix("[Unreleased]: ")
    text = text.replace(
        previous.group(0),
        f"[Unreleased]: {repo}/compare/v{version}...HEAD\n"
        f"[{version}]: {repo}/compare/{previous.group(1)}...v{version}",
    )
    return text, notes


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
