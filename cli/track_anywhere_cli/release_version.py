from __future__ import annotations

import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


BumpPart = Literal["major", "minor", "patch"]

_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_PROJECT_HEADER_RE = re.compile(r"^\s*\[project\]\s*(?:#.*)?$")
_SECTION_HEADER_RE = re.compile(r"^\s*\[[^\]]+\]\s*(?:#.*)?$")
_PROJECT_VERSION_RE = re.compile(r'^(\s*version\s*=\s*")([^"]+)(".*)$')


class ReleaseVersionError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        remediation: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.remediation = remediation


@dataclass(frozen=True)
class VersionBumpPlan:
    project_file: Path
    current_version: str
    next_version: str
    part: BumpPart | None

    @property
    def recommended_tag(self) -> str:
        return f"v{self.next_version}"

    def to_payload(self, *, dry_run: bool, applied: bool) -> dict[str, Any]:
        return {
            "dry_run": dry_run,
            "applied": applied,
            "project_file": str(self.project_file),
            "files": [str(self.project_file)],
            "current_version": self.current_version,
            "next_version": self.next_version,
            "part": self.part,
            "recommended_tag": self.recommended_tag,
            "requires_confirmation": not applied,
            "confirmation": {"flag": "--confirm", "value": self.next_version},
            "checks": [
                {"description": "Run CLI and backend tests.", "command": ["uv", "run", "pytest", "-q"]},
                {
                    "description": "Compile CLI modules.",
                    "command": ["uv", "run", "python", "-m", "compileall", "-q", "cli/track_anywhere_cli"],
                },
            ],
            "next_commands": [
                ["git", "diff", "--", str(self.project_file)],
                ["git", "status", "--short"],
            ],
            "tag_after_commit": {"tag": self.recommended_tag, "command": ["git", "tag", self.recommended_tag]},
        }


def build_version_bump_plan(
    project_file: Path,
    *,
    part: BumpPart = "patch",
    target_version: str | None = None,
) -> VersionBumpPlan:
    resolved_project_file = project_file.expanduser().resolve()
    current_version = read_project_version(resolved_project_file)
    next_version = target_version or bump_version(current_version, part)
    validate_semver(next_version, field_name="target version")
    if next_version == current_version:
        raise ReleaseVersionError(
            "version_unchanged",
            f"Target version is already {current_version}.",
            remediation=[{"description": "Choose a higher target version.", "command": ["ta", "release", "bump", "--to", "<version>"]}],
        )
    return VersionBumpPlan(
        project_file=resolved_project_file,
        current_version=current_version,
        next_version=next_version,
        part=None if target_version else part,
    )


def apply_version_bump(plan: VersionBumpPlan, *, allow_dirty: bool = False) -> None:
    git_root = git_root_for(plan.project_file)
    if git_root is not None and not allow_dirty and git_status(git_root):
        raise ReleaseVersionError(
            "dirty_worktree",
            "Refusing to bump the CLI version with uncommitted changes.",
            remediation=[
                {"description": "Inspect current changes.", "command": ["git", "status", "--short"]},
                {
                    "description": "Allow a dirty worktree explicitly.",
                    "command": ["ta", "release", "bump", "--apply", "--confirm", plan.next_version, "--allow-dirty"],
                },
            ],
        )
    text = plan.project_file.read_text(encoding="utf-8")
    plan.project_file.write_text(replace_project_version(text, plan.current_version, plan.next_version), encoding="utf-8")


def read_project_version(project_file: Path) -> str:
    if not project_file.exists():
        raise ReleaseVersionError(
            "project_file_not_found",
            f"Project file not found: {project_file}",
            remediation=[{"description": "Pass the repository pyproject file.", "command": ["ta", "release", "bump", "--project-file", "pyproject.toml"]}],
        )
    try:
        payload = tomllib.loads(project_file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ReleaseVersionError("invalid_project_file", f"Cannot parse {project_file}: {exc}") from exc
    version = payload.get("project", {}).get("version")
    if not isinstance(version, str):
        raise ReleaseVersionError("missing_project_version", f"{project_file} does not define [project].version.")
    validate_semver(version, field_name="[project].version")
    return version


def bump_version(version: str, part: BumpPart) -> str:
    major, minor, patch = _parse_semver(version, field_name="current version")
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ReleaseVersionError("invalid_bump_part", f"Unsupported bump part: {part}")


def replace_project_version(text: str, current_version: str, next_version: str) -> str:
    lines = text.splitlines(keepends=True)
    in_project = False
    for index, line in enumerate(lines):
        body = line.removesuffix("\n")
        line_ending = "\n" if line.endswith("\n") else ""
        if _PROJECT_HEADER_RE.match(body):
            in_project = True
            continue
        if in_project and _SECTION_HEADER_RE.match(body):
            break
        if in_project:
            match = _PROJECT_VERSION_RE.match(body)
            if match:
                found_version = match.group(2)
                if found_version != current_version:
                    raise ReleaseVersionError(
                        "version_changed",
                        f"Expected version {current_version}, found {found_version}.",
                    )
                lines[index] = f"{match.group(1)}{next_version}{match.group(3)}{line_ending}"
                return "".join(lines)
    raise ReleaseVersionError("missing_project_version", "Could not find [project].version in project file.")


def validate_semver(version: str, *, field_name: str) -> None:
    _parse_semver(version, field_name=field_name)


def git_root_for(path: Path) -> Path | None:
    start = path.parent if path.is_file() else path
    result = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip())


def git_status(git_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(git_root), "status", "--short"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ReleaseVersionError("git_status_failed", result.stderr.strip() or "Failed to inspect git status.")
    return result.stdout.strip()


def _parse_semver(version: str, *, field_name: str) -> tuple[int, int, int]:
    match = _SEMVER_RE.match(version)
    if not match:
        raise ReleaseVersionError(
            "invalid_version",
            f"{field_name} must be plain semver like 1.2.3.",
            remediation=[{"description": "Pass an explicit semver target.", "command": ["ta", "release", "bump", "--to", "1.2.3"]}],
        )
    return tuple(int(part) for part in match.groups())
