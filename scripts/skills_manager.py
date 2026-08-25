#!/usr/bin/env python3
"""Deterministic filesystem operations for the skills-manager Skill.

Mutating commands are dry-runs unless --apply is present. The script deliberately
does not decide installation scope or prompt users; SKILL.md owns those choices.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_FRONTMATTER_KEYS = {
    "name",
    "description",
    "license",
    "compatibility",
    "allowed-tools",
    "metadata",
}
YAML_BOOLEAN_OR_NULL = {
    "null",
    "~",
    "true",
    "false",
    "yes",
    "no",
    "on",
    "off",
}
YAML_NUMBER_RE = re.compile(
    r"[-+]?(?:0x[0-9a-f_]+|0o[0-7_]+|0b[01_]+|(?:\d[\d_]*)(?:\.\d[\d_]*)?(?:e[-+]?\d+)?)",
    re.IGNORECASE,
)
YAML_DATE_RE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}(?:[Tt ][^ ]+)?")
SCHEMA_VERSION = 1
HOST_MODEL_VERSION = 2
DEFAULT_LIBRARY = Path.home() / "SkillsLibrary"
GLOBAL_SKILLS_DIR = Path.home() / ".agents" / "skills"
SUPPORTED_HOSTS = ("codex", "claude-code", "openclaw", "hermes")
SUPPORTED_SCOPES = {
    "legacy": {"global", "project"},
    "codex": {"global", "project"},
    "claude-code": {"global", "project"},
    "openclaw": {"global", "agent"},
    "hermes": {"global", "project"},
}
OVERLAP_DEFAULTS = {"enabled": True, "initial_scan_done": False}
CONTENT_IGNORE_DIRS = {".git", "__pycache__"}
CONTENT_IGNORE_FILES = {".DS_Store"}
CONTENT_DIFF_PATH_LIMIT = 100
OVERLAP_STOP_WORDS = {
    "a",
    "an",
    "and",
    "agent",
    "agents",
    "api",
    "apis",
    "as",
    "ask",
    "asks",
    "be",
    "by",
    "codex",
    "for",
    "from",
    "help",
    "helps",
    "in",
    "into",
    "is",
    "it",
    "local",
    "manage",
    "manages",
    "management",
    "manager",
    "managers",
    "need",
    "needs",
    "of",
    "on",
    "or",
    "project",
    "service",
    "services",
    "skill",
    "skills",
    "support",
    "supports",
    "such",
    "that",
    "the",
    "their",
    "this",
    "through",
    "to",
    "tool",
    "tools",
    "use",
    "used",
    "user",
    "users",
    "uses",
    "using",
    "when",
    "with",
}


class ManagerError(RuntimeError):
    """Expected, user-actionable failure."""


def emit(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def fail(message: str) -> None:
    raise ManagerError(message)


def lexical_path(value: str | Path) -> Path:
    return Path(value).expanduser().absolute()


def resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def lexists(path: Path) -> bool:
    return os.path.lexists(str(path))


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def validate_identifier(value: str, label: str = "name") -> str:
    if len(value) > 64 or not SKILL_NAME_RE.fullmatch(value):
        fail(f"Invalid {label} {value!r}; use lowercase letters, digits, and hyphens.")
    return value


def parse_yaml_string(value: str, key: str) -> str:
    value = value.strip()
    if not value:
        fail(f"Frontmatter {key!r} must be a string")
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            fail(f"Frontmatter {key!r} has an invalid quoted string: {exc}")
        if not isinstance(parsed, str):
            fail(f"Frontmatter {key!r} must be a string")
        return parsed
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            fail(f"Frontmatter {key!r} has an unterminated quoted string")
        return value[1:-1].replace("''", "'")

    value = value.split(" #", 1)[0].rstrip()
    lowered = value.lower()
    if (
        lowered in YAML_BOOLEAN_OR_NULL
        or lowered in {".inf", "+.inf", "-.inf", ".nan"}
        or YAML_NUMBER_RE.fullmatch(value)
        or YAML_DATE_RE.fullmatch(value)
        or value.startswith(("[", "{", "!", "&", "*"))
    ):
        fail(f"Frontmatter {key!r} must be a string")
    if ": " in value:
        fail(f"Frontmatter {key!r} must quote strings containing ': '")
    return value


def strip_yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def read_skill_metadata(skill_dir: Path) -> dict[str, str]:
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        fail(f"Missing SKILL.md in {skill_dir}")
    try:
        lines = skill_file.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        fail(f"SKILL.md is not UTF-8: {exc}")
    if not lines or lines[0].strip() != "---":
        fail(f"SKILL.md in {skill_dir} must start with YAML frontmatter")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        fail(f"SKILL.md in {skill_dir} has unterminated YAML frontmatter")

    values: dict[str, str] = {}
    seen_keys: set[str] = set()
    i = 1
    while i < end:
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if line[0].isspace():
            fail(f"Unexpected frontmatter indentation in {skill_file}: {line}")
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not match:
            fail(f"Unsupported frontmatter syntax in {skill_file}: {line}")
        key, raw = match.group(1), match.group(2)
        if key not in ALLOWED_FRONTMATTER_KEYS:
            allowed = ", ".join(sorted(ALLOWED_FRONTMATTER_KEYS))
            fail(f"Unexpected frontmatter key {key!r}; allowed keys: {allowed}")
        seen_keys.add(key)
        if raw in {">", "|", ">-", "|-"}:
            collected: list[str] = []
            i += 1
            while i < end and (not lines[i] or lines[i][0].isspace()):
                collected.append(lines[i].strip())
                i += 1
            if key in {"name", "description"}:
                values[key] = " ".join(part for part in collected if part)
            continue
        if not raw:
            i += 1
            while i < end and (not lines[i] or lines[i][0].isspace()):
                i += 1
            if key in {"name", "description"}:
                fail(f"Frontmatter {key!r} must be a string")
            continue
        if key in {"name", "description"}:
            values[key] = parse_yaml_string(raw, key)
        i += 1
    missing = {"name", "description"} - seen_keys
    if missing:
        fail(f"Frontmatter is missing required keys: {sorted(missing)}")
    return values


def validate_skill(skill_dir: Path, require_dir_name: bool = True) -> dict[str, Any]:
    skill_dir = resolved(skill_dir)
    errors: list[str] = []
    metadata: dict[str, str] = {}
    metadata_loaded = False
    if not skill_dir.is_dir():
        errors.append(f"Not a directory: {skill_dir}")
    else:
        try:
            metadata = read_skill_metadata(skill_dir)
            metadata_loaded = True
        except ManagerError as exc:
            errors.append(str(exc))

    name = metadata.get("name", "")
    description = metadata.get("description", "")
    if metadata_loaded:
        if not name:
            errors.append("Frontmatter is missing name")
        elif len(name) > 64 or not SKILL_NAME_RE.fullmatch(name):
            errors.append("Skill name must use lowercase letters, digits, and hyphens")
        if not description:
            errors.append("Frontmatter is missing description")
        elif "TODO" in description:
            errors.append("Skill description still contains TODO text")
        elif "<" in description or ">" in description:
            errors.append("Skill description cannot contain angle brackets")
        elif len(description) > 1024:
            errors.append("Skill description cannot exceed 1024 characters")
        if require_dir_name and name and skill_dir.name != name:
            errors.append(f"Directory name {skill_dir.name!r} does not match Skill name {name!r}")
    return {
        "path": str(skill_dir),
        "name": name or None,
        "description": description or None,
        "valid": not errors,
        "errors": errors,
    }


def skill_content_manifest(skill_dir: Path) -> dict[str, dict[str, Any]]:
    """Return a deterministic, portable manifest for version comparison."""
    root = resolved(skill_dir)
    if not root.is_dir():
        fail(f"Cannot fingerprint a non-directory Skill: {root}")

    manifest: dict[str, dict[str, Any]] = {}
    for current_text, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(current_text)
        kept_dirs: list[str] = []
        for dirname in sorted(dirnames):
            if dirname in CONTENT_IGNORE_DIRS:
                continue
            path = current / dirname
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                manifest[relative] = {"type": "symlink", "target": os.readlink(path)}
            else:
                manifest[relative] = {"type": "directory"}
                kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in sorted(filenames):
            if (
                filename in CONTENT_IGNORE_DIRS
                or filename in CONTENT_IGNORE_FILES
                or filename.endswith(".pyc")
            ):
                continue
            path = current / filename
            relative = path.relative_to(root).as_posix()
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                manifest[relative] = {"type": "symlink", "target": os.readlink(path)}
                continue
            if stat.S_ISREG(metadata.st_mode):
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                manifest[relative] = {
                    "type": "file",
                    "sha256": digest.hexdigest(),
                    "executable": bool(metadata.st_mode & 0o111),
                }
                continue
            manifest[relative] = {
                "type": "other",
                "mode": stat.S_IFMT(metadata.st_mode),
            }
    return dict(sorted(manifest.items()))


def manifest_fingerprint(manifest: dict[str, dict[str, Any]]) -> str:
    encoded = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compare_skill_contents(current: Path, incoming: Path) -> dict[str, Any]:
    current_manifest = skill_content_manifest(current)
    incoming_manifest = skill_content_manifest(incoming)
    current_paths = set(current_manifest)
    incoming_paths = set(incoming_manifest)
    added = sorted(incoming_paths - current_paths)
    removed = sorted(current_paths - incoming_paths)
    changed = sorted(
        path
        for path in current_paths & incoming_paths
        if current_manifest[path] != incoming_manifest[path]
    )

    def limited(paths: list[str]) -> list[str]:
        return paths[:CONTENT_DIFF_PATH_LIMIT]

    return {
        "identical": not added and not removed and not changed,
        "current_fingerprint": manifest_fingerprint(current_manifest),
        "incoming_fingerprint": manifest_fingerprint(incoming_manifest),
        "ignored": {
            "names": sorted(CONTENT_IGNORE_DIRS),
            "files": sorted(CONTENT_IGNORE_FILES),
            "suffixes": [".pyc"],
        },
        "added": limited(added),
        "removed": limited(removed),
        "changed": limited(changed),
        "counts": {
            "current_entries": len(current_manifest),
            "incoming_entries": len(incoming_manifest),
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
        },
        "paths_truncated": any(
            len(paths) > CONTENT_DIFF_PATH_LIMIT for paths in (added, removed, changed)
        ),
    }


class Library:
    def __init__(self, root: Path):
        self.root = lexical_path(root)
        self.skills = self.root / "skills"
        self.groups = self.root / "groups"
        self.internal = self.root / ".skills-manager"
        self.backups = self.internal / "backups"
        self.staging = self.internal / "staging"
        self.state_file = self.internal / "state.json"

    def default_state(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "host_model_version": HOST_MODEL_VERSION,
            "migration_status": "not-asked",
            "exposures": {},
            "installations": {},
            "skill_scopes": {},
            "backups": [],
            "overlap": dict(OVERLAP_DEFAULTS),
        }

    def ensure_layout(self) -> None:
        for path in (self.skills, self.groups, self.backups, self.staging):
            path.mkdir(parents=True, exist_ok=True)
        if not self.state_file.exists():
            self.save_state(self.default_state())

    def load_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return self.default_state()
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            fail(f"Cannot read state file {self.state_file}: {exc}")
        if state.get("schema_version") != SCHEMA_VERSION:
            fail(f"Unsupported state schema in {self.state_file}")
        state.setdefault("migration_status", "not-asked")
        state.setdefault("exposures", {})
        state.setdefault("host_model_version", HOST_MODEL_VERSION)
        state.setdefault("installations", {})
        state.setdefault("skill_scopes", {})
        state.setdefault("backups", [])
        if state["host_model_version"] != HOST_MODEL_VERSION:
            fail(f"Unsupported host model in {self.state_file}")
        if not isinstance(state["installations"], dict):
            fail(f"Invalid installations data in {self.state_file}")
        overlap = state.setdefault("overlap", {})
        if not isinstance(overlap, dict):
            fail(f"Invalid overlap configuration in {self.state_file}")
        overlap.setdefault("enabled", OVERLAP_DEFAULTS["enabled"])
        overlap.setdefault("initial_scan_done", OVERLAP_DEFAULTS["initial_scan_done"])
        if not isinstance(overlap["enabled"], bool):
            fail(f"Invalid overlap enabled flag in {self.state_file}")
        if not isinstance(overlap["initial_scan_done"], bool):
            fail(f"Invalid overlap initial scan flag in {self.state_file}")
        return state

    def save_state(self, state: dict[str, Any]) -> None:
        self.internal.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="state-", suffix=".json", dir=self.internal)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2, sort_keys=True, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.state_file)
        except Exception:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
            raise

    def skill_path(self, name: str) -> Path:
        return self.skills / validate_identifier(name, "Skill name")

    def group_path(self, name: str) -> Path:
        return self.groups / f"{validate_identifier(name, 'group name')}.yaml"

    def backup_path(self, name: str, kind: str) -> Path:
        safe_name = validate_identifier(name)
        return self.backups / f"{timestamp()}-{kind}-{safe_name}-{uuid.uuid4().hex[:8]}"

    def stage_path(self, name: str) -> Path:
        safe_name = validate_identifier(name)
        return self.staging / f"{safe_name}-{uuid.uuid4().hex}"

    def rollback_path(self, name: str, kind: str) -> Path:
        safe_name = validate_identifier(name)
        return self.staging / f".{kind}-rollback-v1-{safe_name}-{uuid.uuid4().hex}"


def list_canonical_skills(lib: Library) -> list[str]:
    if not lib.skills.is_dir():
        return []
    return sorted(
        child.name
        for child in lib.skills.iterdir()
        if child.is_dir() and not child.name.startswith(".")
    )


def parse_group(path: Path) -> dict[str, Any]:
    if not path.is_file():
        fail(f"Group manifest not found: {path}")
    name: str | None = None
    skills: list[str] = []
    in_skills = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("name:"):
            name = strip_yaml_scalar(line.split(":", 1)[1])
            in_skills = False
        elif line == "skills:" or line == "skills: []":
            in_skills = line == "skills:"
        elif in_skills and line.startswith("- "):
            skill = strip_yaml_scalar(line[2:])
            validate_identifier(skill, "Skill name")
            if skill not in skills:
                skills.append(skill)
        else:
            fail(f"Unsupported group manifest syntax in {path}: {raw}")
    if not name:
        fail(f"Group manifest has no name: {path}")
    validate_identifier(name, "group name")
    if path.stem != name:
        fail(f"Group file name {path.stem!r} does not match manifest name {name!r}")
    return {"name": name, "skills": skills, "path": str(path)}


def group_text(name: str, skills: Iterable[str]) -> str:
    ordered = sorted(dict.fromkeys(skills))
    lines = [f"name: {name}"]
    if ordered:
        lines.append("skills:")
        lines.extend(f"  - {skill}" for skill in ordered)
    else:
        lines.append("skills: []")
    return "\n".join(lines) + "\n"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
        raise


def list_groups(lib: Library) -> list[str]:
    if not lib.groups.is_dir():
        return []
    return sorted(path.stem for path in lib.groups.glob("*.yaml") if path.is_file())


def normalized_host(host: str | None) -> str:
    value = host or "legacy"
    if value != "legacy" and value not in SUPPORTED_HOSTS:
        fail(f"Unsupported host {value!r}; choose one of {SUPPORTED_HOSTS}")
    return value


def default_host_root(host: str) -> Path:
    if host == "codex":
        return lexical_path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    if host == "claude-code":
        return lexical_path(Path.home() / ".claude")
    if host == "openclaw":
        return lexical_path(os.environ.get("OPENCLAW_STATE_DIR", str(Path.home() / ".openclaw")))
    if host == "hermes":
        return lexical_path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    fail(f"Host {host!r} has no native root")


def claude_code_executable_candidates() -> tuple[Path, ...]:
    return (
        Path.home() / ".local" / "bin" / "claude",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
        Path("/usr/bin/claude"),
    )


def claude_code_detected() -> bool:
    """Return whether this user profile appears to have Claude Code installed."""
    if shutil.which("claude") is not None:
        return True
    return any(
        path.is_file() and os.access(path, os.X_OK)
        for path in claude_code_executable_candidates()
    )


def manager_claude_link() -> Path:
    return default_host_root("claude-code") / "skills" / "skills-manager"


def is_reserved_manager_bootstrap_exposure(
    link_text: str,
    item: dict[str, Any],
    canonical: Path,
) -> bool:
    target_text = item.get("target")
    return bool(
        link_text == str(GLOBAL_SKILLS_DIR / "skills-manager")
        and item.get("skill") == "skills-manager"
        and item.get("host", "legacy") == "legacy"
        and item.get("scope") == "global"
        and target_text
        and resolved(Path(target_text)) == resolved(canonical)
    )


def validate_host_options(
    host: str,
    scope: str,
    project: str | None,
    workspace: str | None,
    state_dir: str | None,
    profile_home: str | None,
) -> None:
    if scope not in SUPPORTED_SCOPES[host]:
        allowed = ", ".join(sorted(SUPPORTED_SCOPES[host]))
        fail(f"Scope {scope!r} is not valid for host {host!r}; choose {allowed}")
    if project and not (scope == "project" and host in {"legacy", "codex", "claude-code", "hermes"}):
        fail("--project is valid only for a project scope")
    if workspace and not (host == "openclaw" and scope == "agent"):
        fail("--workspace is valid only for OpenClaw agent scope")
    if state_dir and not (host == "openclaw" and scope == "global"):
        fail("--state-dir is valid only for OpenClaw global scope")
    if profile_home and not (host == "hermes" and scope == "global"):
        fail("--profile-home is valid only for Hermes global scope")


def scope_link(
    name: str,
    scope: str,
    project: str | None,
    host: str | None = None,
    workspace: str | None = None,
    state_dir: str | None = None,
    profile_home: str | None = None,
) -> tuple[Path, Path | None]:
    validate_identifier(name, "Skill name")
    selected_host = normalized_host(host)
    validate_host_options(
        selected_host, scope, project, workspace, state_dir, profile_home
    )

    if selected_host == "legacy":
        if scope == "global":
            return GLOBAL_SKILLS_DIR / name, None
        project_root = lexical_path(project) if project else None
        suffix = Path(".agents") / "skills"
    elif selected_host == "codex":
        if scope == "global":
            return default_host_root(selected_host) / "skills" / name, None
        project_root = lexical_path(project) if project else None
        suffix = Path(".agents") / "skills"
    elif selected_host == "claude-code":
        if scope == "global":
            return default_host_root(selected_host) / "skills" / name, None
        project_root = lexical_path(project) if project else None
        suffix = Path(".claude") / "skills"
    elif selected_host == "openclaw":
        if scope == "global":
            root = lexical_path(state_dir) if state_dir else default_host_root(selected_host)
            return root / "skills" / name, None
        project_root = lexical_path(workspace) if workspace else None
        suffix = Path("skills")
    else:
        if scope == "global":
            root = lexical_path(profile_home) if profile_home else default_host_root(selected_host)
            return root / "skills" / name, None
        project_root = lexical_path(project) if project else None
        suffix = Path(".hermes") / "skills"

    label = "OpenClaw agent workspace" if selected_host == "openclaw" else "Project or module root"
    if project_root is None:
        option = "--workspace" if selected_host == "openclaw" else "--project"
        fail(f"{label} scope requires {option} with an existing directory")
    if not project_root.is_dir():
        fail(f"{label} does not exist: {project_root}")
    return project_root / suffix / name, project_root


def link_status(link: Path, target: Path) -> str:
    if not lexists(link):
        return "create"
    if link.is_symlink() and resolved(link) == resolved(target):
        return "already-correct"
    if link.is_symlink():
        return f"conflict-symlink:{resolved(link)}"
    return "conflict-existing-path"


def ensure_scope_compatible(
    name: str,
    scope: str,
    target: Path,
    host: str | None = None,
    state_dir: str | None = None,
    profile_home: str | None = None,
) -> None:
    selected_host = normalized_host(host)
    if scope == "global":
        return
    global_link, _ = scope_link(
        name,
        "global",
        None,
        selected_host,
        state_dir=state_dir,
        profile_home=profile_home,
    )
    global_status = link_status(global_link, target)
    if global_status == "already-correct":
        fail(
            f"Skill {name!r} is globally exposed for host {selected_host!r}; "
            f"unexpose that host-global binding before adding {scope!r} scope"
        )


def record_backup(state: dict[str, Any], path: Path, source: Path, kind: str) -> None:
    state["backups"].append(
        {
            "path": str(path),
            "source": str(source),
            "kind": kind,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def record_exposure(
    state: dict[str, Any],
    link: Path,
    target: Path,
    skill: str,
    scope: str,
    project: Path | None,
    host: str | None = None,
    workspace: Path | None = None,
    state_dir: Path | None = None,
    profile_home: Path | None = None,
) -> None:
    selected_host = normalized_host(host)
    state["exposures"][str(link)] = {
        "skill": skill,
        "target": str(target),
        "host": selected_host,
        "scope": scope,
        "project": str(project) if project else None,
        "workspace": str(workspace) if workspace else None,
        "state_dir": str(state_dir) if state_dir else None,
        "profile_home": str(profile_home) if profile_home else None,
    }


def record_installation(
    state: dict[str, Any],
    skill: str,
    host: str,
    scope: str,
    link: Path | None,
    project: Path | None = None,
    workspace: Path | None = None,
    state_dir: Path | None = None,
    profile_home: Path | None = None,
) -> None:
    selected_host = normalized_host(host)
    if selected_host == "legacy":
        state["skill_scopes"][skill] = scope
        return
    records = state["installations"].setdefault(skill, [])
    if not isinstance(records, list):
        fail(f"Invalid installation records for Skill {skill!r}")
    record = {
        "host": selected_host,
        "scope": scope,
        "link": str(link) if link else None,
        "project": str(project) if project else None,
        "workspace": str(workspace) if workspace else None,
        "state_dir": str(state_dir) if state_dir else None,
        "profile_home": str(profile_home) if profile_home else None,
    }
    if link is None and any(
        item.get("host") == selected_host
        and item.get("scope") == scope
        and item.get("link")
        for item in records
    ):
        return
    identity = (
        selected_host,
        scope,
        record["link"],
        record["project"],
        record["workspace"],
        record["state_dir"],
        record["profile_home"],
    )
    kept = []
    for item in records:
        existing_identity = (
            item.get("host"),
            item.get("scope"),
            item.get("link"),
            item.get("project"),
            item.get("workspace"),
            item.get("state_dir"),
            item.get("profile_home"),
        )
        if existing_identity == identity:
            continue
        if (
            link is not None
            and item.get("host") == selected_host
            and item.get("scope") == scope
            and not item.get("link")
        ):
            continue
        kept.append(item)
    kept.append(record)
    kept.sort(key=lambda item: json.dumps(item, sort_keys=True))
    state["installations"][skill] = kept


def remove_installation_for_link(state: dict[str, Any], skill: str, link: Path, host: str) -> None:
    records = state["installations"].get(skill, [])
    if not isinstance(records, list):
        return
    remaining = [
        item
        for item in records
        if not (item.get("host") == host and item.get("link") == str(link))
    ]
    if remaining:
        state["installations"][skill] = remaining
    else:
        state["installations"].pop(skill, None)


def clear_legacy_scope_if_unexposed(state: dict[str, Any], skill: str) -> bool:
    has_legacy_exposure = any(
        item.get("skill") == skill and item.get("host", "legacy") == "legacy"
        for item in state["exposures"].values()
    )
    if has_legacy_exposure:
        return False
    return state["skill_scopes"].pop(skill, None) is not None


def exposure_requirements(item: dict[str, Any]) -> list[dict[str, str]]:
    host = item["host"]
    scope = item["scope"]
    if host == "openclaw" and scope == "agent":
        return [
            {
                "type": "openclaw-allow-symlink-target",
                "target": str(item["target"]),
                "note": "Trust this canonical Skill target before OpenClaw loads the workspace symlink.",
            }
        ]
    if host == "hermes" and scope == "project":
        return [
            {
                "type": "hermes-project-trust",
                "project": str(item["context_root"]),
                "note": "Hermes must trust the project before loading project Skills.",
            }
        ]
    return []


def apply_exposure_items(lib: Library, items: list[dict[str, Any]]) -> list[str]:
    state = lib.load_state()
    created: list[Path] = []
    try:
        for item in items:
            record_installation(
                state,
                item["skill"],
                item["host"],
                item["scope"],
                item["link"],
                project=item.get("project"),
                workspace=item.get("workspace"),
                state_dir=item.get("state_dir"),
                profile_home=item.get("profile_home"),
            )
            if item["status"] == "already-correct":
                record_exposure(
                    state,
                    item["link"],
                    item["target"],
                    item["skill"],
                    item["scope"],
                    item.get("project"),
                    item["host"],
                    item.get("workspace"),
                    item.get("state_dir"),
                    item.get("profile_home"),
                )
                continue
            item["link"].parent.mkdir(parents=True, exist_ok=True)
            os.symlink(item["target"], item["link"], target_is_directory=True)
            created.append(item["link"])
            if resolved(item["link"]) != resolved(item["target"]):
                fail(f"Created link did not resolve to target: {item['link']}")
            record_exposure(
                state,
                item["link"],
                item["target"],
                item["skill"],
                item["scope"],
                item.get("project"),
                item["host"],
                item.get("workspace"),
                item.get("state_dir"),
                item.get("profile_home"),
            )
        lib.save_state(state)
    except Exception:
        for path in reversed(created):
            if path.is_symlink():
                path.unlink()
        raise
    return [str(path) for path in created]


def apply_exposures(
    lib: Library,
    items: list[dict[str, Any]],
    scope: str,
    project_root: Path | None,
    host: str | None = None,
) -> list[str]:
    """Apply one homogeneous exposure plan; retained for the command API."""
    return apply_exposure_items(lib, items)


def plan_exposures(
    lib: Library,
    names: Iterable[str],
    scope: str,
    project: str | None,
    host: str | None = None,
    workspace: str | None = None,
    state_dir: str | None = None,
    profile_home: str | None = None,
) -> tuple[list[dict[str, Any]], Path | None]:
    items: list[dict[str, Any]] = []
    project_root: Path | None = None
    selected_host = normalized_host(host)
    state = lib.load_state()
    for name in names:
        target = lib.skill_path(name)
        result = validate_skill(target)
        if not result["valid"]:
            fail(f"Cannot expose invalid or missing Skill {name!r}: {result['errors']}")
        ensure_scope_compatible(
            name,
            scope,
            target,
            selected_host,
            state_dir=state_dir,
            profile_home=profile_home,
        )
        link, this_project = scope_link(
            name,
            scope,
            project,
            selected_host,
            workspace,
            state_dir,
            profile_home,
        )
        if project_root is None:
            project_root = this_project
        status = link_status(link, target)
        if status.startswith("conflict"):
            fail(f"Exposure conflict at {link}: {status}")
        recorded = state["exposures"].get(str(link))
        if recorded and recorded.get("host", "legacy") != selected_host:
            fail(
                f"Exposure record conflict at {link}: belongs to host "
                f"{recorded.get('host', 'legacy')!r}; unexpose that binding first"
            )
        context_root = this_project
        resolved_state_dir = (
            lexical_path(state_dir)
            if selected_host == "openclaw" and scope == "global" and state_dir
            else default_host_root("openclaw")
            if selected_host == "openclaw" and scope == "global"
            else None
        )
        resolved_profile_home = (
            lexical_path(profile_home)
            if selected_host == "hermes" and scope == "global" and profile_home
            else default_host_root("hermes")
            if selected_host == "hermes" and scope == "global"
            else None
        )
        item = {
            "skill": name,
            "target": target,
            "link": link,
            "status": status,
            "host": selected_host,
            "scope": scope,
            "context_root": context_root,
            "project": this_project if scope == "project" else None,
            "workspace": this_project if selected_host == "openclaw" and scope == "agent" else None,
            "state_dir": resolved_state_dir,
            "profile_home": resolved_profile_home,
        }
        item["requirements"] = exposure_requirements(item)
        items.append(item)
    return items, project_root


def copy_skill_to_stage(lib: Library, source: Path, name: str) -> Path:
    stage = lib.stage_path(name)
    shutil.copytree(source, stage, symlinks=True)
    result = validate_skill(stage, require_dir_name=False)
    if not result["valid"] or result["name"] != name:
        shutil.rmtree(stage, ignore_errors=True)
        fail(f"Staged Skill validation failed: {result['errors']}")
    return stage


def skill_link_snapshot(
    state: dict[str, Any], name: str, target: Path
) -> dict[str, Any]:
    candidates: dict[str, bool] = {}
    for link_text, item in state["exposures"].items():
        if isinstance(item, dict) and item.get("skill") == name:
            candidates[str(link_text)] = True
    records = state["installations"].get(name, [])
    if isinstance(records, list):
        for record in records:
            if isinstance(record, dict) and record.get("link"):
                candidates[str(record["link"])] = True

    known_global_links = [GLOBAL_SKILLS_DIR / name]
    for host in SUPPORTED_HOSTS:
        host_link, _ = scope_link(name, "global", None, host)
        known_global_links.append(host_link)
    for link in known_global_links:
        if lexists(link):
            candidates.setdefault(str(link), False)

    correct: list[str] = []
    issues: list[dict[str, Any]] = []
    for link_text, recorded in sorted(candidates.items()):
        status = link_status(Path(link_text), target)
        if status == "already-correct":
            correct.append(link_text)
        elif recorded:
            issues.append({"link": link_text, "status": status})
    return {"correct": correct, "preexisting_issues": issues}


def replacement_impact(
    lib: Library, state: dict[str, Any], name: str, target: Path
) -> dict[str, Any]:
    records = state["installations"].get(name, [])
    installations = records if isinstance(records, list) else []
    exposures: list[dict[str, Any]] = []
    host_scopes: set[str] = set()
    for record in installations:
        if not isinstance(record, dict):
            continue
        host_scopes.add(f"{record.get('host')}:{record.get('scope')}")
    for link_text, item in sorted(state["exposures"].items()):
        if not isinstance(item, dict) or item.get("skill") != name:
            continue
        host = item.get("host", "legacy")
        scope = item.get("scope")
        host_scopes.add(f"{host}:{scope}")
        exposures.append(
            {
                "link": link_text,
                "host": host,
                "scope": scope,
                "status": link_status(Path(link_text), target),
            }
        )
    snapshot = skill_link_snapshot(state, name, target)
    return {
        "host_scopes": sorted(host_scopes),
        "installations": installations,
        "exposures": exposures,
        "correct_links_to_revalidate": snapshot["correct"],
        "preexisting_link_issues": snapshot["preexisting_issues"],
        "groups": group_memberships(lib, name),
        "all_installations_switch_together": True,
    }


def validate_promoted_replacement(
    target: Path, name: str, incoming_fingerprint: str, expected_links: list[str]
) -> dict[str, Any]:
    validation = validate_skill(target)
    if not validation["valid"] or validation["name"] != name:
        fail(f"Promoted Skill failed validation: {validation['errors']}")
    promoted_fingerprint = manifest_fingerprint(skill_content_manifest(target))
    if promoted_fingerprint != incoming_fingerprint:
        fail("Promoted Skill content fingerprint does not match the approved incoming version")
    link_results = [
        {"link": link_text, "status": link_status(Path(link_text), target)}
        for link_text in expected_links
    ]
    broken = [item for item in link_results if item["status"] != "already-correct"]
    if broken:
        fail(f"Replacement changed previously correct host links: {broken}")
    return {
        "canonical": validation,
        "fingerprint": promoted_fingerprint,
        "links": link_results,
        "runtime_availability_verified": False,
    }


def cmd_init(args: argparse.Namespace, lib: Library) -> None:
    plan = {
        "action": "initialize-library",
        "apply": args.apply,
        "library": str(lib.root),
        "create": [str(lib.skills), str(lib.groups), str(lib.backups), str(lib.state_file)],
        "git": "unchanged",
    }
    if args.apply:
        lib.ensure_layout()
    emit(plan)


def cmd_status(args: argparse.Namespace, lib: Library) -> None:
    state = lib.load_state()
    canonical = lib.skill_path("skills-manager")
    global_link = GLOBAL_SKILLS_DIR / "skills-manager"
    canonical_valid = bool(
        canonical.is_dir()
        and not canonical.is_symlink()
        and validate_skill(canonical)["valid"]
    )
    global_link_status = link_status(global_link, canonical)
    skills = list_canonical_skills(lib)
    global_skills = [name for name in skills if state["skill_scopes"].get(name) == "global"]
    project_skills = [name for name in skills if state["skill_scopes"].get(name) == "project"]
    installed_names = {
        name for name, records in state["installations"].items() if isinstance(records, list) and records
    }
    installation_status: dict[str, list[dict[str, Any]]] = {}
    for name, records in sorted(state["installations"].items()):
        target = lib.skill_path(name)
        if not isinstance(records, list):
            installation_status[name] = [{"status": "invalid-installation-records"}]
            continue
        status_records: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                status_records.append({"status": "invalid-installation-record"})
                continue
            item = dict(record)
            link_text = record.get("link")
            item["status"] = (
                link_status(Path(str(link_text)), target)
                if link_text
                else "canonical-only"
            )
            item["expected_target"] = str(target)
            status_records.append(item)
        installation_status[name] = status_records
    unclassified_skills = [
        name
        for name in skills
        if state["skill_scopes"].get(name) not in {"global", "project"}
        and name not in installed_names
    ]
    global_exposure_status = {
        name: link_status(GLOBAL_SKILLS_DIR / name, lib.skill_path(name)) for name in global_skills
    }
    recorded_project_exposures: dict[str, list[str]] = {}
    for item in state["exposures"].values():
        if item.get("scope") != "project" or not item.get("skill") or not item.get("project"):
            continue
        recorded_project_exposures.setdefault(item["skill"], []).append(item["project"])
    recorded_project_exposures = {
        name: sorted(set(projects))
        for name, projects in sorted(recorded_project_exposures.items())
    }
    claude_link = manager_claude_link()
    claude_status = link_status(claude_link, canonical)
    bootstrap_modes = {
        "codex": "bootstrap",
        "openclaw": "bootstrap",
        "hermes": "bootstrap-external-directory",
    }
    manager_host_exposure_status = {
        host: {
            "link": str(global_link),
            "status": global_link_status,
            "mode": mode,
        }
        for host, mode in bootstrap_modes.items()
    }
    manager_host_exposure_status["claude-code"] = {
        "link": str(claude_link),
        "status": claude_status,
        "mode": "compatibility-link",
    }
    claude_detected = claude_code_detected()
    claude_compatibility = {
        "detected": claude_detected,
        "link": str(claude_link),
        "status": claude_status,
        "offer": bool(claude_detected and claude_status != "already-correct"),
    }
    legacy_bindings_pending: list[str] = []
    for name in skills:
        legacy_exposures = [
            (link_text, item)
            for link_text, item in state["exposures"].items()
            if item.get("skill") == name and item.get("host", "legacy") == "legacy"
        ]
        if name == "skills-manager":
            unexpected = any(
                not is_reserved_manager_bootstrap_exposure(link_text, item, canonical)
                for link_text, item in legacy_exposures
            )
            invalid_scope_marker = (
                name in state["skill_scopes"]
                and state["skill_scopes"].get(name) != "global"
            )
            if unexpected or invalid_scope_marker:
                legacy_bindings_pending.append(name)
            continue
        if name in state["skill_scopes"] or legacy_exposures:
            legacy_bindings_pending.append(name)
    emit(
        {
            "library": str(lib.root),
            "library_exists": lib.root.is_dir(),
            "canonical_manager": str(canonical),
            "canonical_manager_valid": canonical_valid,
            "global_manager_link": str(global_link),
            "global_manager_link_status": global_link_status,
            "manager_bootstrap": {
                "link": str(global_link),
                "status": global_link_status,
                "target": str(canonical),
            },
            "claude_code_detected": claude_detected,
            "claude_compatibility_offer": claude_compatibility["offer"],
            "claude_compatibility": claude_compatibility,
            "migration_status": state["migration_status"],
            "host_model_version": state["host_model_version"],
            "installations": state["installations"],
            "installation_status": installation_status,
            "legacy_bindings_pending": legacy_bindings_pending,
            "manager_host_exposure_status": manager_host_exposure_status,
            "skills": skills,
            "global_skills": global_skills,
            "global_exposure_status": global_exposure_status,
            "project_skills": project_skills,
            "unclassified_skills": unclassified_skills,
            "recorded_project_exposures": recorded_project_exposures,
            "groups": list_groups(lib),
            "recorded_exposures": len(state["exposures"]),
            "recoverable_backups": len(state["backups"]),
            "overlap": dict(state["overlap"]),
        }
    )


def overlap_terms(value: str) -> set[str]:
    """Extract deliberately explainable, low-cost lexical terms."""
    terms: set[str] = set()
    normalized = unicodedata.normalize("NFKC", value).casefold()
    for raw in re.findall(r"[^\W_]+", normalized, flags=re.UNICODE):
        term = raw
        if len(term) > 4 and term.endswith("ies"):
            term = term[:-3] + "y"
        elif len(term) > 4 and term.endswith("s") and not term.endswith("ss"):
            term = term[:-1]
        if len(term) >= 2 and term not in OVERLAP_STOP_WORDS:
            terms.add(term)
    return terms


def overlap_item(
    path: Path,
    lib: Library,
    state: dict[str, Any],
    kind: str,
    require_dir_name: bool,
) -> dict[str, Any]:
    lexical = lexical_path(path)
    validation = validate_skill(lexical, require_dir_name=require_dir_name)
    if not validation["valid"]:
        fail(f"Cannot scan invalid or missing Skill {lexical}: {validation['errors']}")
    name = str(validation["name"])
    details: dict[str, Any] = {
        "name": name,
        "description": str(validation["description"]),
        "path": str(lexical),
        "kind": kind,
        "_target": str(resolved(lexical)),
    }
    if kind == "canonical":
        exposures = [
            {
                "link": link,
                "target": item.get("target"),
                "host": item.get("host", "legacy"),
                "scope": item.get("scope"),
                "project": item.get("project"),
                "workspace": item.get("workspace"),
            }
            for link, item in sorted(state["exposures"].items())
            if item.get("skill") == name
        ]
        global_link = GLOBAL_SKILLS_DIR / name
        if (
            link_status(global_link, lexical) == "already-correct"
            and str(global_link) not in {item["link"] for item in exposures}
        ):
            exposures.append(
                {
                    "link": str(global_link),
                    "target": str(lexical),
                    "host": "legacy",
                    "scope": "global",
                    "project": None,
                    "workspace": None,
                }
            )
        exposures.sort(key=lambda item: item["link"])
        details.update(
            {
                "scope": state["skill_scopes"].get(name),
                "installations": state["installations"].get(name, []),
                "exposures": exposures,
                "group_memberships": group_memberships(lib, name),
            }
        )
    else:
        details.update(
            {"scope": None, "installations": [], "exposures": [], "group_memberships": []}
        )
    return details


def public_overlap_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def overlap_pair(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {"left": public_overlap_item(left), "right": public_overlap_item(right)}


def overlap_signals(left: dict[str, Any], right: dict[str, Any]) -> tuple[bool, list[dict[str, Any]], list[str]]:
    left_name = overlap_terms(left["name"])
    right_name = overlap_terms(right["name"])
    left_description = overlap_terms(left["description"])
    right_description = overlap_terms(right["description"])
    shared_name = sorted(left_name & right_name)
    shared_description = sorted(left_description & right_description)
    cross_name_description = sorted(
        (left_name & right_description) | (right_name & left_description)
    )
    shared_terms = sorted((left_name | left_description) & (right_name | right_description))
    name_score = difflib.SequenceMatcher(
        None, " ".join(sorted(left_name)), " ".join(sorted(right_name))
    ).ratio()
    description_score = difflib.SequenceMatcher(
        None,
        " ".join(sorted(left_description)),
        " ".join(sorted(right_description)),
    ).ratio()

    signals: list[dict[str, Any]] = []
    if shared_name:
        signals.append({"type": "shared-name-terms", "terms": shared_name})
    if shared_description:
        signals.append({"type": "shared-description-terms", "terms": shared_description})
    if cross_name_description:
        signals.append({"type": "name-description-cross-terms", "terms": cross_name_description})
    if name_score >= 0.72:
        signals.append({"type": "similar-names", "score": round(name_score, 3)})
    if description_score >= 0.62:
        signals.append({"type": "similar-descriptions", "score": round(description_score, 3)})

    is_candidate = bool(
        shared_name
        or (len(shared_description) >= 3 and description_score >= 0.35)
        or cross_name_description
        or name_score >= 0.72
        or description_score >= 0.62
    )
    return is_candidate, signals, shared_terms


def cmd_overlap_scan(args: argparse.Namespace, lib: Library) -> None:
    state = lib.load_state()
    overlap = state["overlap"]
    candidate_values = list(getattr(args, "candidate", []) or [])
    payload: dict[str, Any] = {
        "action": "overlap-scan",
        "enabled": overlap["enabled"],
        "initial_scan_done": overlap["initial_scan_done"],
        "candidate_paths": [str(lexical_path(path)) for path in candidate_values],
        "semantic_review_notice": (
            "Lexical signals identify pairs for Agent semantic review; they do not determine "
            "that Skills have high functional overlap."
        ),
    }
    if not overlap["enabled"]:
        payload.update(
            {
                "skipped": True,
                "skip_reason": "overlap scanning is disabled",
                "mode": "disabled",
                "scanned_items": [],
                "lexical_candidates": [],
                "same_name": [],
                "skipped_same_target": [],
                "summary": {"pairs_considered": 0},
            }
        )
        emit(payload)
        return

    canonical_items = [
        overlap_item(lib.skill_path(name), lib, state, "canonical", True)
        for name in list_canonical_skills(lib)
    ]
    candidate_items = [
        overlap_item(Path(path), lib, state, "candidate", False) for path in candidate_values
    ]
    if candidate_items:
        pairs = [
            (candidate, canonical)
            for candidate in candidate_items
            for canonical in canonical_items
        ]
        pairs.extend(
            (left, right)
            for index, left in enumerate(candidate_items)
            for right in candidate_items[index + 1 :]
        )
        mode = "candidates"
        scanned_items = canonical_items + candidate_items
    else:
        pairs = [
            (left, right)
            for index, left in enumerate(canonical_items)
            for right in canonical_items[index + 1 :]
        ]
        mode = "canonical-pairs"
        scanned_items = canonical_items

    lexical_candidates: list[dict[str, Any]] = []
    same_name: list[dict[str, Any]] = []
    skipped_same_target: list[dict[str, Any]] = []
    filtered_out = 0
    for left, right in pairs:
        pair = overlap_pair(left, right)
        if left["_target"] == right["_target"]:
            skipped_same_target.append(pair)
            continue
        if left["name"] == right["name"]:
            same_name.append(pair)
            continue
        is_candidate, signals, shared_terms = overlap_signals(left, right)
        if not is_candidate:
            filtered_out += 1
            continue
        pair["shared_terms"] = shared_terms
        pair["signals"] = signals
        lexical_candidates.append(pair)

    payload.update(
        {
            "skipped": False,
            "mode": mode,
            "scanned_items": [public_overlap_item(item) for item in scanned_items],
            "lexical_candidates": lexical_candidates,
            "same_name": same_name,
            "skipped_same_target": skipped_same_target,
            "summary": {
                "items_scanned": len(scanned_items),
                "pairs_considered": len(pairs),
                "lexical_candidates": len(lexical_candidates),
                "same_name": len(same_name),
                "skipped_same_target": len(skipped_same_target),
                "filtered_out": filtered_out,
            },
        }
    )
    emit(payload)


def cmd_overlap_set(args: argparse.Namespace, lib: Library) -> None:
    state = lib.load_state()
    new_value = args.setting == "on"
    payload = {
        "action": "overlap-set",
        "apply": args.apply,
        "previous": state["overlap"]["enabled"],
        "new": new_value,
    }
    if args.apply:
        lib.ensure_layout()
        state["overlap"]["enabled"] = new_value
        lib.save_state(state)
    emit(payload)


def cmd_overlap_mark_initial_scan(args: argparse.Namespace, lib: Library) -> None:
    state = lib.load_state()
    payload = {
        "action": "overlap-mark-initial-scan",
        "apply": args.apply,
        "previous": state["overlap"]["initial_scan_done"],
        "new": True,
    }
    if args.apply:
        lib.ensure_layout()
        state["overlap"]["initial_scan_done"] = True
        lib.save_state(state)
    emit(payload)


def cmd_validate(args: argparse.Namespace, lib: Library) -> None:
    result = validate_skill(lexical_path(args.skill_dir))
    emit(result)
    if not result["valid"]:
        raise SystemExit(1)


def cmd_discover(args: argparse.Namespace, lib: Library) -> None:
    requested_hosts = getattr(args, "host", None) or []
    selected_hosts = sorted(set(requested_hosts)) if requested_hosts else list(SUPPORTED_HOSTS)
    include_legacy = (
        bool(getattr(args, "include_legacy", False)) if requested_hosts else True
    )
    roots: list[tuple[str, Path]] = []
    if include_legacy:
        roots.append(("legacy-user", GLOBAL_SKILLS_DIR))
    for host in selected_hosts:
        roots.append((f"{host}-global", default_host_root(host) / "skills"))

    project_hosts = [host for host in selected_hosts if host != "openclaw"]
    projects = getattr(args, "project", None) or []
    if projects and not project_hosts:
        fail("--project requires codex, claude-code, or hermes in --host")
    for project in projects:
        root = lexical_path(project)
        if not root.is_dir():
            fail(f"Project or module root does not exist: {root}")
        if "codex" in project_hosts:
            roots.append(("codex-project", root / ".agents" / "skills"))
        if "claude-code" in project_hosts:
            roots.append(("claude-code-project", root / ".claude" / "skills"))
        if "hermes" in project_hosts:
            roots.append(("hermes-project", root / ".hermes" / "skills"))

    workspaces = getattr(args, "workspace", None) or []
    if workspaces and "openclaw" not in selected_hosts:
        fail("--workspace requires openclaw in --host")
    for workspace in workspaces:
        root = lexical_path(workspace)
        if not root.is_dir():
            fail(f"OpenClaw agent workspace does not exist: {root}")
        roots.append(("openclaw-agent", root / "skills"))

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for scope, root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda item: item.name):
            if child.name.startswith(".") or child.name == ".system":
                continue
            if child.absolute() == (GLOBAL_SKILLS_DIR / "skills-manager").absolute():
                continue
            key = str(child.absolute())
            if key in seen or (not child.is_dir() and not child.is_symlink()):
                continue
            seen.add(key)
            target = resolved(child)
            managed = target.parent == resolved(lib.skills)
            validation = validate_skill(target)
            candidates.append(
                {
                    "path": str(child),
                    "resolved_target": str(target),
                    "kind": "symlink" if child.is_symlink() else "directory",
                    "scope": scope,
                    "already_managed": managed,
                    "name": validation["name"],
                    "valid": validation["valid"],
                    "errors": validation["errors"],
                }
            )
    emit(
        {
            "hosts": selected_hosts,
            "include_legacy": include_legacy,
            "roots": [{"scope": scope, "path": str(path)} for scope, path in roots],
            "candidates": candidates,
        }
    )


def cmd_adopt(args: argparse.Namespace, lib: Library) -> None:
    source = resolved(lexical_path(args.source))
    validation = validate_skill(source, require_dir_name=False)
    if not validation["valid"]:
        fail(f"Source Skill is invalid: {validation['errors']}")
    name = validate_identifier(str(validation["name"]), "Skill name")
    target = lib.skill_path(name)
    target_present = lexists(target)
    if name == "skills-manager" and not target_present:
        fail("Use initialize --source <path> --host <host> to install Skills Manager itself")
    if target_present and (target.is_symlink() or not target.is_dir()):
        fail(f"Existing canonical Skill is not a real directory: {target}")
    if target_present and resolved(target) == source:
        emit({"action": "adopt", "result": "already-canonical", "skill": name, "target": str(target)})
        return
    canonical_location = resolved(target)
    if source in canonical_location.parents or canonical_location in source.parents:
        fail(
            "Source and canonical paths must not contain one another; choose a separate "
            f"incoming directory ({source} vs {canonical_location})"
        )

    comparison: dict[str, Any] | None = None
    impact: dict[str, Any] | None = None
    state = lib.load_state()
    if target_present:
        target_validation = validate_skill(target)
        if not target_validation["valid"]:
            fail(f"Existing canonical Skill is invalid: {target}")
        comparison = compare_skill_contents(target, source)
        impact = replacement_impact(lib, state, name, target)
        if comparison["identical"]:
            emit(
                {
                    "action": "reuse-existing",
                    "apply": args.apply,
                    "mutation": False,
                    "result": "content-identical",
                    "skill": name,
                    "source": str(source),
                    "target": str(target),
                    "comparison": comparison,
                    "installations": impact,
                    "next_step": (
                        "Reuse this canonical copy; expose it only for the requested host and "
                        "scope if that installation is not already correct."
                    ),
                }
            )
            return
        if not args.replace:
            emit(
                {
                    "action": "version-choice-required",
                    "apply": False,
                    "mutation": False,
                    "skill": name,
                    "incoming": str(source),
                    "current": str(target),
                    "comparison": comparison,
                    "installations": impact,
                    "warning": (
                        "Choosing the incoming version replaces the one canonical copy, so every "
                        "recorded host installation for this Skill switches together."
                    ),
                    "choices": [
                        {
                            "choice": "use-existing",
                            "mutation": False,
                            "next_step": "Expose the existing canonical copy for the requested host if needed.",
                        },
                        {
                            "choice": "use-incoming",
                            "mutation": True,
                            "next_command": ["adopt", str(source), "--replace"],
                            "note": (
                                "Dry-run this replacement; if it is conflict-free and unchanged, "
                                "repeat it immediately with --apply without another confirmation."
                            ),
                        },
                        {"choice": "cancel", "mutation": False},
                    ],
                }
            )
            return

    plan = {
        "action": "replace" if target_present else "adopt",
        "apply": args.apply,
        "skill": name,
        "source": str(source),
        "target": str(target),
        "existing_target": target_present,
        "comparison": comparison,
        "installations": impact,
        "rollback_backup": {
            "retention": "transaction-only" if target_present else "not-needed",
            "directory": str(lib.staging),
            "delete_after_local_validation": target_present,
        },
    }
    if not args.apply:
        emit(plan)
        return

    lib.ensure_layout()
    stage = copy_skill_to_stage(lib, source, name)
    rollback: Path | None = None
    target_created = False
    committed = False
    expected_fingerprint = (
        comparison["incoming_fingerprint"]
        if comparison
        else manifest_fingerprint(skill_content_manifest(source))
    )
    staged_fingerprint = manifest_fingerprint(skill_content_manifest(stage))
    if staged_fingerprint != expected_fingerprint:
        shutil.rmtree(stage, ignore_errors=True)
        fail("Staged Skill content changed while it was being copied; review the source and retry")
    link_snapshot = skill_link_snapshot(state, name, target) if target_present else {
        "correct": [],
        "preexisting_issues": [],
    }
    try:
        if target_present:
            rollback = lib.rollback_path(name, "replace")
            shutil.move(str(target), str(rollback))
        os.replace(stage, target)
        target_created = True
        plan["validation"] = validate_promoted_replacement(
            target,
            name,
            expected_fingerprint,
            link_snapshot["correct"],
        )
        lib.save_state(state)
        committed = True
    except Exception as exc:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        rollback_error: Exception | None = None
        try:
            if target_created and lexists(target):
                if target.is_symlink() or not target.is_dir():
                    fail(f"Cannot remove unexpected promoted target during rollback: {target}")
                shutil.rmtree(target)
            if lexists(target):
                fail(f"Cannot restore the previous canonical Skill because the target still exists: {target}")
            if rollback and rollback.exists():
                shutil.move(str(rollback), str(target))
        except Exception as rollback_exc:
            rollback_error = rollback_exc
        if rollback_error:
            fail(f"Adoption failed ({exc}); rollback also failed ({rollback_error})")
        raise

    cleanup_pending: dict[str, str] | None = None
    rollback_deleted = rollback is None
    if committed and rollback and rollback.exists():
        try:
            shutil.rmtree(rollback)
            rollback_deleted = True
        except Exception as cleanup_exc:
            cleanup_pending = {"path": str(rollback), "error": str(cleanup_exc)}
    plan["backup"] = None
    plan["rollback_backup_deleted"] = rollback_deleted
    plan["cleanup_pending"] = cleanup_pending
    plan["preexisting_link_issues"] = link_snapshot["preexisting_issues"]
    emit(plan)


def cmd_expose(args: argparse.Namespace, lib: Library) -> None:
    if args.skill == "skills-manager":
        fail("Use initialize --host <host> for the reserved Skills Manager bootstrap")
    host = getattr(args, "host", None)
    items, project_root = plan_exposures(
        lib,
        [args.skill],
        args.scope,
        getattr(args, "project", None),
        host,
        getattr(args, "workspace", None),
        getattr(args, "state_dir", None),
        getattr(args, "profile_home", None),
    )
    payload = {
        "action": "expose",
        "apply": args.apply,
        "host": normalized_host(host),
        "scope": args.scope,
        "project": str(project_root) if project_root else None,
        "links": [
            {
                "skill": item["skill"],
                "link": str(item["link"]),
                "target": str(item["target"]),
                "status": item["status"],
                "requirements": item["requirements"],
            }
            for item in items
        ],
    }
    if args.apply:
        lib.ensure_layout()
        payload["created"] = apply_exposures(
            lib, items, args.scope, project_root, normalized_host(host)
        )
    emit(payload)


def cmd_repair(args: argparse.Namespace, lib: Library) -> None:
    name = validate_identifier(args.skill, "Skill name")
    if name == "skills-manager":
        fail("Use initialize --host <host> to repair the reserved Skills Manager bootstrap")
    host = normalized_host(args.host)
    target = lib.skill_path(name)
    validation = validate_skill(target)
    if target.is_symlink() or not validation["valid"]:
        fail(f"Cannot repair an exposure for an invalid canonical Skill: {validation['errors']}")
    ensure_scope_compatible(
        name,
        args.scope,
        target,
        host,
        state_dir=getattr(args, "state_dir", None),
        profile_home=getattr(args, "profile_home", None),
    )
    link, context_root = scope_link(
        name,
        args.scope,
        getattr(args, "project", None),
        host,
        getattr(args, "workspace", None),
        getattr(args, "state_dir", None),
        getattr(args, "profile_home", None),
    )
    status = link_status(link, target)
    if status == "conflict-existing-path":
        fail(f"Refusing to repair a non-symlink path: {link}")

    state = lib.load_state()
    recorded = state["exposures"].get(str(link))
    if recorded and recorded.get("host", "legacy") != host:
        fail(
            f"Exposure record conflict at {link}: belongs to host "
            f"{recorded.get('host', 'legacy')!r}"
        )

    project_root = context_root if args.scope == "project" else None
    workspace_root = context_root if host == "openclaw" and args.scope == "agent" else None
    state_root = (
        lexical_path(args.state_dir)
        if host == "openclaw" and args.scope == "global" and args.state_dir
        else default_host_root("openclaw")
        if host == "openclaw" and args.scope == "global"
        else None
    )
    profile_root = (
        lexical_path(args.profile_home)
        if host == "hermes" and args.scope == "global" and args.profile_home
        else default_host_root("hermes")
        if host == "hermes" and args.scope == "global"
        else None
    )
    requirement_item = {
        "host": host,
        "scope": args.scope,
        "target": target,
        "context_root": context_root,
    }
    operation = (
        "register" if status == "already-correct"
        else "create" if status == "create"
        else "repoint"
    )
    previous_link_text = os.readlink(link) if link.is_symlink() else None
    payload = {
        "action": "repair-exposure",
        "apply": args.apply,
        "skill": name,
        "host": host,
        "scope": args.scope,
        "project": str(project_root) if project_root else None,
        "workspace": str(workspace_root) if workspace_root else None,
        "link": str(link),
        "target": str(target),
        "status": status,
        "operation": operation,
        "previous_link_target": previous_link_text,
        "requirements": exposure_requirements(requirement_item),
        "touches_real_directories": False,
    }
    if not args.apply:
        emit(payload)
        return

    lib.ensure_layout()
    filesystem_changed = False
    try:
        if status.startswith("conflict-symlink"):
            link.unlink()
            filesystem_changed = True
        if status != "already-correct":
            link.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, link, target_is_directory=True)
            filesystem_changed = True
        if link_status(link, target) != "already-correct":
            fail(f"Repaired link did not resolve to the canonical target: {link}")
        record_installation(
            state,
            name,
            host,
            args.scope,
            link,
            project=project_root,
            workspace=workspace_root,
            state_dir=state_root,
            profile_home=profile_root,
        )
        record_exposure(
            state,
            link,
            target,
            name,
            args.scope,
            project_root,
            host,
            workspace_root,
            state_root,
            profile_root,
        )
        lib.save_state(state)
    except Exception as exc:
        if filesystem_changed:
            try:
                if lexists(link):
                    if not link.is_symlink() or resolved(link) != resolved(target):
                        fail(f"Cannot roll back repair because the link changed again: {link}")
                    link.unlink()
                if previous_link_text is not None:
                    os.symlink(previous_link_text, link, target_is_directory=True)
            except Exception as rollback_exc:
                fail(f"Repair failed ({exc}); rollback also failed ({rollback_exc})")
        raise
    payload["result"] = "repaired" if filesystem_changed else "registered"
    emit(payload)


def cmd_set_scope(args: argparse.Namespace, lib: Library) -> None:
    names = list(dict.fromkeys(validate_identifier(name, "Skill name") for name in args.skills))
    if "skills-manager" in names:
        fail("Use initialize --host <host> for the reserved Skills Manager bootstrap")
    state = lib.load_state()
    host = normalized_host(getattr(args, "host", None))
    project = getattr(args, "project", None)
    workspace = getattr(args, "workspace", None)
    state_dir = getattr(args, "state_dir", None)
    profile_home = getattr(args, "profile_home", None)
    validate_host_options(host, args.scope, project, workspace, state_dir, profile_home)
    items: list[dict[str, Any]] = []
    for name in names:
        target = lib.skill_path(name)
        result = validate_skill(target)
        if not result["valid"]:
            fail(f"Cannot classify invalid or missing Skill {name!r}: {result['errors']}")
        global_link, _ = scope_link(
            name,
            "global",
            None,
            host,
            state_dir=state_dir,
            profile_home=profile_home,
        )
        global_status = link_status(global_link, target)
        if args.scope == "global" and global_status != "already-correct":
            fail(
                f"Cannot mark {name!r} global for host {host!r} without a correct link; "
                f"use expose --host {host} --scope global"
            )
        ensure_scope_compatible(
            name,
            args.scope,
            target,
            host,
            state_dir=state_dir,
            profile_home=profile_home,
        )
        items.append(
            {
                "skill": name,
                "target": str(target),
                "previous_scope": state["skill_scopes"].get(name),
                "previous_installations": state["installations"].get(name, []),
                "new_scope": args.scope,
            }
        )

    payload: dict[str, Any] = {
        "action": "set-scope",
        "apply": args.apply,
        "host": host,
        "scope": args.scope,
        "skills": items,
    }
    if args.apply:
        for name in names:
            record_installation(
                state,
                name,
                host,
                args.scope,
                None,
                project=lexical_path(project) if project else None,
                workspace=lexical_path(workspace) if workspace else None,
                state_dir=lexical_path(state_dir) if state_dir else None,
                profile_home=lexical_path(profile_home) if profile_home else None,
            )
        lib.ensure_layout()
        lib.save_state(state)
    emit(payload)


def cmd_unset_scope(args: argparse.Namespace, lib: Library) -> None:
    names = list(dict.fromkeys(validate_identifier(name, "Skill name") for name in args.skills))
    host = normalized_host(args.host)
    if host == "legacy":
        fail("Use legacy unexpose to clear a legacy scope marker")
    if args.scope not in SUPPORTED_SCOPES[host]:
        allowed = ", ".join(sorted(SUPPORTED_SCOPES[host]))
        fail(f"Scope {args.scope!r} is not valid for host {host!r}; choose {allowed}")
    state = lib.load_state()
    plans: list[dict[str, Any]] = []
    for name in names:
        records = state["installations"].get(name, [])
        if not isinstance(records, list):
            fail(f"Invalid installation records for Skill {name!r}")
        matching = [
            item
            for item in records
            if item.get("host") == host and item.get("scope") == args.scope
        ]
        if not matching:
            fail(f"No {host}:{args.scope} installation exists for Skill {name!r}")
        linked = [item for item in matching if item.get("link")]
        if linked:
            fail(f"Unexpose linked installations before clearing their scope: {linked}")
        plans.append({"skill": name, "remove": matching})

    payload = {
        "action": "unset-scope",
        "apply": args.apply,
        "host": host,
        "scope": args.scope,
        "skills": plans,
    }
    if args.apply:
        for plan in plans:
            name = plan["skill"]
            records = state["installations"].get(name, [])
            remaining = [
                item
                for item in records
                if not (item.get("host") == host and item.get("scope") == args.scope)
            ]
            if remaining:
                state["installations"][name] = remaining
            else:
                state["installations"].pop(name, None)
        lib.ensure_layout()
        lib.save_state(state)
    emit(payload)


def migrate_core(
    lib: Library,
    source: Path,
    scope: str,
    project: str | None,
    apply: bool,
    expected_name: str | None = None,
    host: str | None = None,
    workspace: str | None = None,
    state_dir: str | None = None,
    profile_home: str | None = None,
    additional_hosts: tuple[str, ...] = (),
) -> dict[str, Any]:
    source = lexical_path(source)
    if source.is_symlink():
        fail("Migration source is a symlink; inspect and migrate its real target explicitly")
    validation = validate_skill(source, require_dir_name=False)
    if not validation["valid"]:
        fail(f"Migration source is invalid: {validation['errors']}")
    name = validate_identifier(str(validation["name"]), "Skill name")
    if expected_name and name != expected_name:
        fail(f"Expected Skill {expected_name!r}, found {name!r}")
    target = lib.skill_path(name)
    selected_host = normalized_host(host)
    ensure_scope_compatible(
        name,
        scope,
        target,
        selected_host,
        state_dir=state_dir,
        profile_home=profile_home,
    )
    link, project_root = scope_link(
        name,
        scope,
        project,
        selected_host,
        workspace,
        state_dir,
        profile_home,
    )
    additional_hosts = tuple(dict.fromkeys(normalized_host(value) for value in additional_hosts))
    if "legacy" in additional_hosts or selected_host in additional_hosts:
        fail("Additional migration hosts must be distinct explicit hosts")
    if resolved(source) == resolved(target):
        items, project_root = plan_exposures(
            lib,
            [name],
            scope,
            project,
            selected_host,
            workspace,
            state_dir,
            profile_home,
        )
        for additional_host in additional_hosts:
            additional_items, _ = plan_exposures(
                lib,
                [name],
                scope,
                project,
                additional_host,
            )
            items.extend(additional_items)
        payload = {
            "action": "migrate",
            "result": "already-canonical",
            "apply": apply,
            "skill": name,
            "source": str(source),
            "target": str(target),
            "host": selected_host,
            "link": str(items[0]["link"]),
            "requirements": items[0]["requirements"],
            "additional_exposures": [
                {
                    "host": item["host"],
                    "link": str(item["link"]),
                    "status": item["status"],
                    "requirements": item["requirements"],
                }
                for item in items[1:]
            ],
        }
        if apply:
            lib.ensure_layout()
            payload["created"] = apply_exposure_items(lib, items)
        return payload
    if target.exists():
        fail(f"Canonical destination already exists: {target}")
    exposure_items: list[dict[str, Any]] = []
    planning_state = lib.load_state()
    for exposure_host, exposure_link, exposure_project in [
        (selected_host, link, project_root),
        *[
            (additional_host, scope_link(name, scope, project, additional_host)[0], None)
            for additional_host in additional_hosts
        ],
    ]:
        status = link_status(exposure_link, target)
        source_is_link_path = (
            source == exposure_link and source.is_dir() and not source.is_symlink()
        )
        source_symlink_target = (
            os.readlink(exposure_link)
            if exposure_link.is_symlink()
            and resolved(exposure_link) == resolved(source)
            else None
        )
        if (
            status.startswith("conflict")
            and not source_is_link_path
            and source_symlink_target is None
        ):
            fail(f"Exposure conflict at {exposure_link}: {status}")
        recorded = planning_state["exposures"].get(str(exposure_link))
        if recorded and recorded.get("host", "legacy") != exposure_host:
            fail(
                f"Exposure record conflict at {exposure_link}: belongs to host "
                f"{recorded.get('host', 'legacy')!r}; unexpose that binding first"
            )
        exposure_items.append(
            {
                "skill": name,
                "target": target,
                "link": exposure_link,
                "status": status,
                "host": exposure_host,
                "scope": scope,
                "context_root": exposure_project,
                "project": exposure_project if scope == "project" else None,
                "workspace": None,
                "state_dir": None,
                "profile_home": None,
                "requirements": [],
                "replace_source_symlink_target": source_symlink_target,
            }
        )

    payload: dict[str, Any] = {
        "action": "migrate",
        "apply": apply,
        "skill": name,
        "source": str(source),
        "target": str(target),
        "host": selected_host,
        "scope": scope,
        "project": str(project_root) if project_root else None,
        "link": str(link),
        "additional_exposures": [
            {
                "host": item["host"],
                "link": str(item["link"]),
                "status": item["status"],
                "requirements": item["requirements"],
                "repoints_source_symlink": item["replace_source_symlink_target"] is not None,
            }
            for item in exposure_items[1:]
        ],
        "repoints_source_symlink": (
            exposure_items[0]["replace_source_symlink_target"] is not None
        ),
        "source_becomes_backup": True,
    }
    legacy_state = lib.load_state()
    legacy_source_record = legacy_state["exposures"].get(str(source))
    clears_legacy_binding = bool(
        selected_host != "legacy"
        and legacy_source_record
        and legacy_source_record.get("skill") == name
        and legacy_source_record.get("host", "legacy") == "legacy"
    )
    payload["clears_legacy_binding"] = clears_legacy_binding
    payload["legacy_scope_present"] = legacy_state["skill_scopes"].get(name)
    requirement_item = {
        "host": selected_host,
        "scope": scope,
        "target": target,
        "context_root": project_root,
    }
    payload["requirements"] = exposure_requirements(requirement_item)
    if not apply:
        return payload

    lib.ensure_layout()
    stage = copy_skill_to_stage(lib, resolved(source), name)
    backup = lib.backup_path(name, "migrate")
    created_links: list[Path] = []
    replaced_source_symlinks: list[tuple[Path, str]] = []
    target_created = False
    source_moved = False
    try:
        os.replace(stage, target)
        target_created = True
        shutil.move(str(source), str(backup))
        source_moved = True
        for item in exposure_items:
            exposure_link = item["link"]
            source_symlink_target = item["replace_source_symlink_target"]
            if source_symlink_target is not None:
                exposure_link.unlink()
                replaced_source_symlinks.append(
                    (exposure_link, source_symlink_target)
                )
            if item["status"] != "already-correct":
                exposure_link.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(target, exposure_link, target_is_directory=True)
                created_links.append(exposure_link)
            if resolved(exposure_link) != resolved(target):
                fail(f"Created link did not resolve to target: {exposure_link}")
        state = lib.load_state()
        record_backup(state, backup, source, "migrate")
        if clears_legacy_binding:
            existing_legacy = state["exposures"].get(str(source))
            if (
                existing_legacy
                and existing_legacy.get("skill") == name
                and existing_legacy.get("host", "legacy") == "legacy"
            ):
                state["exposures"].pop(str(source), None)
            clear_legacy_scope_if_unexposed(state, name)
        resolved_state_dir = (
            lexical_path(state_dir)
            if state_dir
            else default_host_root("openclaw")
            if selected_host == "openclaw" and scope == "global"
            else None
        )
        resolved_profile_home = (
            lexical_path(profile_home)
            if profile_home
            else default_host_root("hermes")
            if selected_host == "hermes" and scope == "global"
            else None
        )
        resolved_workspace = (
            project_root if selected_host == "openclaw" and scope == "agent" else None
        )
        resolved_project = project_root if scope == "project" else None
        for item in exposure_items:
            item_project = resolved_project if item["host"] == selected_host else None
            item_workspace = resolved_workspace if item["host"] == selected_host else None
            item_state_dir = resolved_state_dir if item["host"] == selected_host else None
            item_profile_home = resolved_profile_home if item["host"] == selected_host else None
            record_exposure(
                state,
                item["link"],
                target,
                name,
                scope,
                item_project,
                item["host"],
                item_workspace,
                item_state_dir,
                item_profile_home,
            )
            record_installation(
                state,
                name,
                item["host"],
                scope,
                item["link"],
                project=item_project,
                workspace=item_workspace,
                state_dir=item_state_dir,
                profile_home=item_profile_home,
            )
        lib.save_state(state)
    except Exception:
        for created_link in reversed(created_links):
            if created_link.is_symlink():
                created_link.unlink()
        if source_moved and backup.exists() and not source.exists():
            shutil.move(str(backup), str(source))
        for replaced_link, original_target in reversed(replaced_source_symlinks):
            if not lexists(replaced_link):
                os.symlink(original_target, replaced_link, target_is_directory=True)
        if target_created and target.exists():
            shutil.rmtree(target, ignore_errors=True)
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        raise
    payload["backup"] = str(backup)
    payload["created"] = [str(path) for path in created_links]
    return payload


def cmd_migrate(args: argparse.Namespace, lib: Library) -> None:
    validation = validate_skill(lexical_path(args.source), require_dir_name=False)
    if validation.get("valid") and validation.get("name") == "skills-manager":
        fail("Use initialize --source <path> --host <host> for Skills Manager itself")
    emit(
        migrate_core(
            lib,
            lexical_path(args.source),
            args.scope,
            getattr(args, "project", None),
            args.apply,
            host=getattr(args, "host", None),
            workspace=getattr(args, "workspace", None),
            state_dir=getattr(args, "state_dir", None),
            profile_home=getattr(args, "profile_home", None),
        )
    )


def cmd_initialize_manager(args: argparse.Namespace, lib: Library) -> None:
    requested_host = getattr(args, "host", None)
    if requested_host is not None:
        normalized_host(requested_host)
    if getattr(args, "state_dir", None) or getattr(args, "profile_home", None):
        fail(
            "Skills Manager always uses the ~/.agents/skills bootstrap; "
            "--state-dir and --profile-home do not apply"
        )
    canonical = lib.skill_path("skills-manager")
    canonical_validation = validate_skill(canonical) if canonical.exists() else {"valid": False}
    if (
        canonical.is_dir()
        and not canonical.is_symlink()
        and canonical_validation["valid"]
        and canonical_validation.get("name") == "skills-manager"
    ):
        source = canonical
    else:
        source = lexical_path(args.source) if args.source else lexical_path(Path(__file__).parent.parent)
    claude_link = manager_claude_link()
    claude_status = link_status(claude_link, canonical)
    claude_requested = requested_host == "claude-code"
    source_is_claude_entry = (
        (source == claude_link and source.is_dir() and not source.is_symlink())
        or (claude_link.is_symlink() and resolved(claude_link) == resolved(source))
    )
    if (
        claude_requested
        and claude_status.startswith("conflict")
        and not source_is_claude_entry
    ):
        fail(f"Claude Code compatibility entry conflict at {claude_link}: {claude_status}")

    bootstrap_status = link_status(GLOBAL_SKILLS_DIR / "skills-manager", canonical)
    payload = migrate_core(
        lib,
        source,
        "global",
        None,
        args.apply,
        expected_name="skills-manager",
        host=None,
        additional_hosts=("claude-code",) if claude_requested else (),
    )
    payload["action"] = "initialize-manager"
    payload["host"] = "bootstrap"
    payload["requested_host"] = requested_host
    payload["bootstrap"] = {
        "link": str(GLOBAL_SKILLS_DIR / "skills-manager"),
        "status": (
            link_status(GLOBAL_SKILLS_DIR / "skills-manager", canonical)
            if args.apply
            else bootstrap_status
        ),
        "target": str(canonical),
    }
    claude_detected = claude_code_detected()
    claude_compatibility: dict[str, Any] = {
        "detected": claude_detected,
        "requested": claude_requested,
        "link": str(claude_link),
        "status": claude_status,
        "offer": bool(
            claude_detected
            and not claude_requested
            and claude_status != "already-correct"
        ),
        "created": [],
    }
    if claude_requested and args.apply:
        claude_compatibility["created"] = [
            path for path in payload.get("created", []) if path == str(claude_link)
        ]
        claude_compatibility["status"] = link_status(claude_link, canonical)
    payload["claude_compatibility"] = claude_compatibility
    if args.apply:
        if claude_requested:
            payload["next_step"] = (
                "Use Skills Manager through the Claude Code compatibility entry on the next turn; "
                "restart Claude Code if it does not appear."
            )
        elif claude_compatibility["offer"]:
            payload["next_step"] = (
                "Skills Manager is installed through the shared bootstrap. Claude Code was detected; "
                "ask whether to create its compatibility entry before running initialize "
                "--host claude-code."
            )
        else:
            payload["next_step"] = (
                "Use Skills Manager through the shared bootstrap on the next turn; "
                "restart the agent client if it does not appear."
            )
    emit(payload)


def cmd_unexpose(args: argparse.Namespace, lib: Library) -> None:
    target = lib.skill_path(args.skill)
    host = normalized_host(getattr(args, "host", None))
    if args.skill == "skills-manager" and host == "legacy":
        fail("The ~/.agents/skills/skills-manager bootstrap is reserved and cannot be unexposed")
    link, project_root = scope_link(
        args.skill,
        args.scope,
        getattr(args, "project", None),
        host,
        getattr(args, "workspace", None),
        getattr(args, "state_dir", None),
        getattr(args, "profile_home", None),
    )
    state = lib.load_state()
    recorded_item = state["exposures"].get(str(link))
    recorded = recorded_item is not None
    if (
        host == "codex"
        and args.scope == "project"
        and not recorded_item
        and state["skill_scopes"].get(args.skill) == "project"
    ):
        fail(
            f"Cannot prove Codex ownership of {link}; legacy project state exists. "
            "Migrate or clear the legacy binding explicitly first"
        )
    if recorded_item and recorded_item.get("host", "legacy") != host:
        fail(
            f"Exposure at {link} belongs to host {recorded_item.get('host', 'legacy')!r}, "
            f"not {host!r}"
        )
    if not lexists(link):
        payload = {
            "action": "unexpose",
            "apply": args.apply,
            "skill": args.skill,
            "host": host,
            "link": str(link),
            "result": "missing-link",
            "clear_stale_record": recorded,
            "clear_legacy_scope": host == "legacy" and args.skill in state["skill_scopes"],
            "scope_marker": state["skill_scopes"].get(args.skill),
        }
        if args.apply and (recorded or (host == "legacy" and args.skill in state["skill_scopes"])):
            state["exposures"].pop(str(link), None)
            remove_installation_for_link(state, args.skill, link, host)
            if host == "legacy":
                clear_legacy_scope_if_unexposed(state, args.skill)
            lib.ensure_layout()
            lib.save_state(state)
        emit(payload)
        return
    if not link.is_symlink():
        fail(f"Refusing to unlink a non-symlink path: {link}")
    if resolved(link) != resolved(target):
        fail(f"Symlink points somewhere else: {link} -> {resolved(link)}")
    payload = {
        "action": "unexpose",
        "apply": args.apply,
        "skill": args.skill,
        "host": host,
        "scope": args.scope,
        "project": str(project_root) if project_root else None,
        "link": str(link),
        "target": str(target),
        "scope_marker": state["skill_scopes"].get(args.skill),
        "clear_legacy_scope": host == "legacy" and args.skill in state["skill_scopes"],
    }
    if args.apply:
        link_removed = False
        try:
            link.unlink()
            link_removed = True
            state["exposures"].pop(str(link), None)
            remove_installation_for_link(state, args.skill, link, host)
            if host == "legacy":
                clear_legacy_scope_if_unexposed(state, args.skill)
            lib.ensure_layout()
            lib.save_state(state)
        except Exception as exc:
            if link_removed:
                try:
                    if lexists(link):
                        fail(f"Cannot roll back unlink because the path was recreated: {link}")
                    os.symlink(target, link, target_is_directory=True)
                except Exception as rollback_exc:
                    fail(f"Unlink failed ({exc}); rollback also failed ({rollback_exc})")
            raise
    emit(payload)


def group_memberships(lib: Library, skill: str) -> list[str]:
    memberships: list[str] = []
    for name in list_groups(lib):
        try:
            if skill in parse_group(lib.group_path(name))["skills"]:
                memberships.append(name)
        except ManagerError:
            memberships.append(f"{name} (invalid manifest)")
    return memberships


def cmd_remove(args: argparse.Namespace, lib: Library) -> None:
    name = validate_identifier(args.skill, "Skill name")
    if name == "skills-manager":
        fail("Skills Manager is reserved; repair its bootstrap instead of removing it")
    target = lib.skill_path(name)
    if not target.is_dir() or target.is_symlink():
        fail(f"Canonical Skill is missing or not a real directory: {target}")
    state = lib.load_state()
    exposure_paths = {
        path for path, item in state["exposures"].items() if item.get("skill") == name
    }
    global_link = GLOBAL_SKILLS_DIR / name
    if link_status(global_link, target) == "already-correct":
        exposure_paths.add(str(global_link))
    for host in SUPPORTED_HOSTS:
        host_link, _ = scope_link(name, "global", None, host)
        if link_status(host_link, target) == "already-correct":
            exposure_paths.add(str(host_link))
    installations = state["installations"].get(name, [])
    memberships = group_memberships(lib, name)
    if exposure_paths:
        fail(f"Remove active or recorded exposures first: {sorted(exposure_paths)}")
    if installations:
        fail(f"Remove host installations first: {installations}")
    if memberships:
        fail(f"Remove Skill from groups first: {memberships}")
    backup = lib.backup_path(name, "remove")
    payload = {
        "action": "remove-canonical",
        "apply": args.apply,
        "skill": name,
        "target": str(target),
        "backup": str(backup),
        "scope": state["skill_scopes"].get(name),
        "installations": installations,
        "permanent_delete": False,
    }
    if args.apply:
        lib.ensure_layout()
        target_moved = False
        try:
            shutil.move(str(target), str(backup))
            target_moved = True
            record_backup(state, backup, target, "remove")
            state["skill_scopes"].pop(name, None)
            state["installations"].pop(name, None)
            lib.save_state(state)
        except Exception as exc:
            if target_moved:
                try:
                    if target.exists():
                        fail(f"Cannot roll back removal because the canonical path was recreated: {target}")
                    shutil.move(str(backup), str(target))
                except Exception as rollback_exc:
                    fail(f"Removal failed ({exc}); rollback also failed ({rollback_exc})")
            raise
    emit(payload)


def cmd_mark_migration(args: argparse.Namespace, lib: Library) -> None:
    state = lib.load_state()
    payload = {
        "action": "mark-migration",
        "apply": args.apply,
        "previous": state["migration_status"],
        "new": args.status,
    }
    if args.apply:
        lib.ensure_layout()
        state["migration_status"] = args.status
        lib.save_state(state)
    emit(payload)


def cmd_group_list(args: argparse.Namespace, lib: Library) -> None:
    groups = []
    for name in list_groups(lib):
        try:
            manifest = parse_group(lib.group_path(name))
            groups.append({"name": name, "skills": manifest["skills"], "valid": True})
        except ManagerError as exc:
            groups.append({"name": name, "skills": [], "valid": False, "error": str(exc)})
    emit({"groups": groups})


def cmd_group_show(args: argparse.Namespace, lib: Library) -> None:
    emit(parse_group(lib.group_path(args.group)))


def cmd_group_create(args: argparse.Namespace, lib: Library) -> None:
    name = validate_identifier(args.group, "group name")
    path = lib.group_path(name)
    if path.exists():
        fail(f"Group already exists: {path}")
    payload = {"action": "group-create", "apply": args.apply, "group": name, "path": str(path)}
    if args.apply:
        lib.ensure_layout()
        atomic_write(path, group_text(name, []))
    emit(payload)


def cmd_group_add(args: argparse.Namespace, lib: Library) -> None:
    manifest = parse_group(lib.group_path(args.group))
    additions = [validate_identifier(name, "Skill name") for name in args.skills]
    missing = [name for name in additions if not lib.skill_path(name).is_dir()]
    if missing:
        fail(f"Cannot add missing canonical Skills: {missing}")
    new_members = sorted(dict.fromkeys(manifest["skills"] + additions))
    payload = {
        "action": "group-add",
        "apply": args.apply,
        "group": args.group,
        "add": sorted(set(additions) - set(manifest["skills"])),
        "skills": new_members,
    }
    if args.apply:
        atomic_write(lib.group_path(args.group), group_text(args.group, new_members))
    emit(payload)


def cmd_group_remove(args: argparse.Namespace, lib: Library) -> None:
    manifest = parse_group(lib.group_path(args.group))
    removals = {validate_identifier(name, "Skill name") for name in args.skills}
    new_members = [name for name in manifest["skills"] if name not in removals]
    payload = {
        "action": "group-remove",
        "apply": args.apply,
        "group": args.group,
        "remove": sorted(removals & set(manifest["skills"])),
        "skills": new_members,
    }
    if args.apply:
        atomic_write(lib.group_path(args.group), group_text(args.group, new_members))
    emit(payload)


def cmd_group_delete(args: argparse.Namespace, lib: Library) -> None:
    manifest = parse_group(lib.group_path(args.group))
    backup = lib.backup_path(args.group, "group-delete").with_suffix(".yaml")
    payload = {
        "action": "group-delete",
        "apply": args.apply,
        "group": args.group,
        "skills": manifest["skills"],
        "backup": str(backup),
    }
    if args.apply:
        lib.ensure_layout()
        source = lib.group_path(args.group)
        source_moved = False
        try:
            shutil.move(str(source), str(backup))
            source_moved = True
            state = lib.load_state()
            record_backup(state, backup, source, "group-delete")
            lib.save_state(state)
        except Exception as exc:
            if source_moved:
                try:
                    if source.exists():
                        fail(f"Cannot roll back group deletion because the source was recreated: {source}")
                    shutil.move(str(backup), str(source))
                except Exception as rollback_exc:
                    fail(f"Group deletion failed ({exc}); rollback also failed ({rollback_exc})")
            raise
    emit(payload)


def cmd_group_rename(args: argparse.Namespace, lib: Library) -> None:
    old = validate_identifier(args.group, "group name")
    new = validate_identifier(args.new_name, "group name")
    manifest = parse_group(lib.group_path(old))
    new_path = lib.group_path(new)
    if new_path.exists():
        fail(f"Destination group already exists: {new_path}")
    payload = {
        "action": "group-rename",
        "apply": args.apply,
        "from": old,
        "to": new,
        "skills": manifest["skills"],
    }
    if args.apply:
        new_created = False
        try:
            atomic_write(new_path, group_text(new, manifest["skills"]))
            new_created = True
            lib.group_path(old).unlink()
        except Exception as exc:
            if new_created:
                try:
                    new_path.unlink()
                except Exception as rollback_exc:
                    fail(f"Group rename failed ({exc}); rollback also failed ({rollback_exc})")
            raise
    emit(payload)


def cmd_group_expose(args: argparse.Namespace, lib: Library) -> None:
    manifest = parse_group(lib.group_path(args.group))
    if not manifest["skills"]:
        fail(f"Group {args.group!r} has no Skills")
    if "skills-manager" in manifest["skills"]:
        fail("Skills Manager cannot be exposed through a group; use initialize --host <host>")
    host = normalized_host(getattr(args, "host", None))
    items, project_root = plan_exposures(
        lib,
        manifest["skills"],
        args.scope,
        getattr(args, "project", None),
        host,
        getattr(args, "workspace", None),
        getattr(args, "state_dir", None),
        getattr(args, "profile_home", None),
    )
    payload = {
        "action": "group-expose",
        "apply": args.apply,
        "group": args.group,
        "host": host,
        "scope": args.scope,
        "project": str(project_root) if project_root else None,
        "links": [
            {
                "skill": item["skill"],
                "link": str(item["link"]),
                "target": str(item["target"]),
                "status": item["status"],
                "requirements": item["requirements"],
            }
            for item in items
        ],
    }
    if args.apply:
        lib.ensure_layout()
        payload["created"] = apply_exposures(lib, items, args.scope, project_root, host)
    emit(payload)


def cmd_doctor(args: argparse.Namespace, lib: Library) -> None:
    issues: list[dict[str, str]] = []
    notices: list[dict[str, str]] = []
    if lib.staging.is_dir():
        for rollback in sorted(lib.staging.glob(".replace-rollback-v1-*")):
            issues.append(
                {
                    "type": "replacement-rollback-cleanup-pending",
                    "path": str(rollback),
                    "detail": (
                        "A transaction-only replacement rollback copy was not deleted; "
                        "inspect the successful replacement before removing it."
                    ),
                }
            )
    for name in list_canonical_skills(lib):
        path = lib.skill_path(name)
        if path.is_symlink():
            issues.append({"type": "canonical-is-symlink", "path": str(path)})
            continue
        result = validate_skill(path)
        for error in result["errors"]:
            issues.append({"type": "invalid-skill", "path": str(path), "detail": error})
    for group in list_groups(lib):
        try:
            manifest = parse_group(lib.group_path(group))
            for skill in manifest["skills"]:
                if not lib.skill_path(skill).is_dir():
                    issues.append({"type": "missing-group-member", "path": str(lib.group_path(group)), "detail": skill})
        except ManagerError as exc:
            issues.append({"type": "invalid-group", "path": str(lib.group_path(group)), "detail": str(exc)})
    state = lib.load_state()
    canonical_names = set(list_canonical_skills(lib))
    canonical_manager = lib.skill_path("skills-manager")
    if "skills-manager" in canonical_names:
        bootstrap_status = link_status(
            GLOBAL_SKILLS_DIR / "skills-manager", canonical_manager
        )
        if bootstrap_status != "already-correct":
            issues.append(
                {
                    "type": "manager-bootstrap-not-ready",
                    "path": str(GLOBAL_SKILLS_DIR / "skills-manager"),
                    "detail": bootstrap_status,
                }
            )
    for skill, scope in sorted(state["skill_scopes"].items()):
        if skill not in canonical_names:
            issues.append({"type": "scope-for-missing-skill", "path": str(lib.skill_path(skill))})
            continue
        if scope not in {"global", "project"}:
            issues.append(
                {"type": "invalid-skill-scope", "path": str(lib.skill_path(skill)), "detail": str(scope)}
            )
            continue
        global_status = link_status(GLOBAL_SKILLS_DIR / skill, lib.skill_path(skill))
        if scope == "global" and global_status != "already-correct":
            issues.append(
                {
                    "type": "global-skill-not-exposed",
                    "path": str(GLOBAL_SKILLS_DIR / skill),
                    "detail": global_status,
                }
            )
        elif scope == "project" and global_status == "already-correct":
            issues.append(
                {
                    "type": "project-skill-globally-exposed",
                    "path": str(GLOBAL_SKILLS_DIR / skill),
                }
            )
        if skill != "skills-manager":
            notices.append(
                {
                    "type": "legacy-binding-pending",
                    "path": str(lib.skill_path(skill)),
                    "detail": "Classify this legacy binding with an explicit host before cleanup.",
                }
            )

    installed_names: set[str] = set()
    for skill, records in sorted(state["installations"].items()):
        if skill not in canonical_names:
            issues.append({"type": "installation-for-missing-skill", "path": str(lib.skill_path(skill))})
            continue
        if not isinstance(records, list):
            issues.append({"type": "invalid-installations", "path": str(lib.skill_path(skill))})
            continue
        if records:
            installed_names.add(skill)
        for record in records:
            if not isinstance(record, dict):
                issues.append({"type": "invalid-installation", "path": str(lib.skill_path(skill))})
                continue
            host = record.get("host")
            scope = record.get("scope")
            if skill == "skills-manager" and host != "claude-code":
                notices.append(
                    {
                        "type": "obsolete-manager-host-installation",
                        "path": str(record.get("link") or lib.skill_path(skill)),
                        "detail": (
                            "Skills Manager uses the shared bootstrap; only Claude Code needs "
                            "a host compatibility installation."
                        ),
                    }
                )
            if host not in SUPPORTED_HOSTS:
                issues.append(
                    {
                        "type": "invalid-installation-host",
                        "path": str(lib.skill_path(skill)),
                        "detail": str(host),
                    }
                )
                continue
            if scope not in SUPPORTED_SCOPES[host]:
                issues.append(
                    {
                        "type": "invalid-installation-scope",
                        "path": str(lib.skill_path(skill)),
                        "detail": str(scope),
                    }
                )
                continue
            link_text = record.get("link")
            if not link_text:
                if scope == "global":
                    issues.append(
                        {
                            "type": "global-installation-without-link",
                            "path": str(lib.skill_path(skill)),
                            "detail": str(host),
                        }
                    )
                continue
            recorded_exposure = state["exposures"].get(str(link_text))
            if not recorded_exposure:
                issues.append(
                    {
                        "type": "installation-without-exposure",
                        "path": str(link_text),
                        "detail": f"{host}:{scope}",
                    }
                )
            elif recorded_exposure.get("host", "legacy") != host:
                issues.append(
                    {
                        "type": "installation-exposure-host-mismatch",
                        "path": str(link_text),
                        "detail": f"{host} != {recorded_exposure.get('host', 'legacy')}",
                    }
                )

    for skill in sorted(canonical_names - set(state["skill_scopes"]) - installed_names):
        if (
            skill == "skills-manager"
            and link_status(GLOBAL_SKILLS_DIR / skill, lib.skill_path(skill)) == "already-correct"
        ):
            continue
        issues.append({"type": "unclassified-skill", "path": str(lib.skill_path(skill))})
    for link_text, item in state["exposures"].items():
        link = Path(link_text)
        target_text = item.get("target")
        if not target_text:
            issues.append({"type": "recorded-exposure-without-target", "path": link_text})
            continue
        target = Path(target_text)
        skill_name = item.get("skill")
        if (
            skill_name == "skills-manager"
            and item.get("host", "legacy") == "legacy"
            and not is_reserved_manager_bootstrap_exposure(
                link_text, item, canonical_manager
            )
        ):
            notices.append(
                {
                    "type": "legacy-binding-pending",
                    "path": link_text,
                    "detail": (
                        "Only the exact ~/.agents/skills/skills-manager bootstrap is reserved; "
                        "inspect this additional legacy binding."
                    ),
                }
            )
        if skill_name in canonical_names and resolved(target) != resolved(lib.skill_path(skill_name)):
            issues.append(
                {
                    "type": "recorded-target-is-not-canonical",
                    "path": link_text,
                    "detail": str(target),
                }
            )
        host = item.get("host", "legacy")
        scope = item.get("scope")
        if host not in SUPPORTED_SCOPES or scope not in SUPPORTED_SCOPES[host]:
            issues.append(
                {
                    "type": "invalid-recorded-exposure",
                    "path": link_text,
                    "detail": f"{host}:{scope}",
                }
            )
        if not lexists(link):
            issues.append({"type": "missing-recorded-link", "path": link_text})
        elif not link.is_symlink():
            issues.append({"type": "recorded-link-is-not-symlink", "path": link_text})
        elif resolved(link) != resolved(target):
            issues.append({"type": "redirected-link", "path": link_text, "detail": str(resolved(link))})
    emit({"healthy": not issues, "issues": issues, "notices": notices})
    if issues:
        raise SystemExit(1)


def add_apply(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--apply", action="store_true", help="Apply the displayed mutation")


def add_scope(parser: argparse.ArgumentParser, *, host_required: bool = False) -> None:
    parser.add_argument("--scope", choices=("global", "project", "agent"), required=True)
    parser.add_argument(
        "--host",
        choices=SUPPORTED_HOSTS,
        required=host_required,
        help="Target agent host; omit only for legacy .agents compatibility",
    )
    parser.add_argument("--project", help="Existing project or module root for project scope")
    parser.add_argument("--workspace", help="Existing OpenClaw agent workspace for agent scope")
    parser.add_argument("--state-dir", help="OpenClaw state directory override for global scope")
    parser.add_argument("--profile-home", help="Hermes profile home override for global scope")


def normalize_cli_args(argv: list[str]) -> list[str]:
    """Map the legacy initialization command without exposing it in help output."""
    normalized = list(argv)
    index = 0
    while index < len(normalized):
        token = normalized[index]
        if token == "--library":
            index += 2
            continue
        if token.startswith("--library="):
            index += 1
            continue
        if token in {"-h", "--help"}:
            return normalized
        if token == "bootstrap":
            normalized[index] = "initialize"
        return normalized
    return normalized


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", default=str(DEFAULT_LIBRARY), help="Central library root")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Initialize the central library without Git")
    add_apply(init)
    init.set_defaults(func=cmd_init)

    status = sub.add_parser("status", help="Show library, onboarding, and initialization status")
    status.set_defaults(func=cmd_status)

    validate = sub.add_parser("validate", help="Validate one Skill directory")
    validate.add_argument("skill_dir")
    validate.set_defaults(func=cmd_validate)

    discover = sub.add_parser("discover", help="Discover migration candidates in approved roots")
    discover.add_argument(
        "--host",
        action="append",
        choices=SUPPORTED_HOSTS,
        help="Approved host to scan; repeat for multiple hosts (omit for legacy all-host behavior)",
    )
    discover.add_argument(
        "--include-legacy",
        action="store_true",
        help="Also scan legacy .agents/skills after separate consent",
    )
    discover.add_argument("--project", action="append", help="Additional project or module root")
    discover.add_argument(
        "--workspace",
        action="append",
        help="Approved OpenClaw agent workspace root; repeat as needed",
    )
    discover.set_defaults(func=cmd_discover)

    adopt = sub.add_parser(
        "adopt",
        help="Adopt, reuse, or compare a completed local Skill with the library",
    )
    adopt.add_argument("source")
    adopt.add_argument(
        "--replace",
        action="store_true",
        help=(
            "Replace a differing canonical copy after the user selects the incoming version; "
            "rollback copy is transaction-only"
        ),
    )
    add_apply(adopt)
    adopt.set_defaults(func=cmd_adopt)

    expose = sub.add_parser("expose", help="Create one scoped Skill symlink")
    expose.add_argument("skill")
    add_scope(expose)
    add_apply(expose)
    expose.set_defaults(func=cmd_expose)

    set_scope = sub.add_parser("set-scope", help="Record the user-selected Skill classification")
    set_scope.add_argument("skills", nargs="+")
    add_scope(set_scope)
    add_apply(set_scope)
    set_scope.set_defaults(func=cmd_set_scope)

    unset_scope = sub.add_parser(
        "unset-scope", help="Remove one canonical-only host installation classification"
    )
    unset_scope.add_argument("skills", nargs="+")
    unset_scope.add_argument("--host", choices=SUPPORTED_HOSTS, required=True)
    unset_scope.add_argument("--scope", choices=("global", "project", "agent"), required=True)
    add_apply(unset_scope)
    unset_scope.set_defaults(func=cmd_unset_scope)

    migrate = sub.add_parser("migrate", help="Move one real Skill directory into the library and expose it")
    migrate.add_argument("source")
    add_scope(migrate)
    add_apply(migrate)
    migrate.set_defaults(func=cmd_migrate)

    initialize = sub.add_parser(
        "initialize",
        help="Initialize the shared Skills Manager bootstrap and optional Claude entry",
    )
    initialize.add_argument("--source", help="Currently active skills-manager directory")
    initialize.add_argument(
        "--host",
        choices=SUPPORTED_HOSTS,
        help=(
            "Requesting host; claude-code creates its compatibility entry, while other hosts "
            "keep only the shared bootstrap"
        ),
    )
    initialize.add_argument(
        "--state-dir",
        help="Retained for CLI compatibility; rejected because initialization uses the bootstrap",
    )
    initialize.add_argument(
        "--profile-home",
        help="Retained for CLI compatibility; rejected because initialization uses the bootstrap",
    )
    add_apply(initialize)
    initialize.set_defaults(func=cmd_initialize_manager)

    unexpose = sub.add_parser("unexpose", help="Remove only one managed scope symlink")
    unexpose.add_argument("skill")
    add_scope(unexpose)
    add_apply(unexpose)
    unexpose.set_defaults(func=cmd_unexpose)

    repair = sub.add_parser(
        "repair",
        help="Repair or register one host symlink without touching real directories",
    )
    repair.add_argument("skill")
    add_scope(repair, host_required=True)
    add_apply(repair)
    repair.set_defaults(func=cmd_repair)

    remove = sub.add_parser("remove", help="Move an unused canonical Skill to recoverable backup")
    remove.add_argument("skill")
    add_apply(remove)
    remove.set_defaults(func=cmd_remove)

    migration = sub.add_parser("mark-migration", help="Record first-run migration prompt status")
    migration.add_argument("status", choices=("declined", "accepted", "completed"))
    add_apply(migration)
    migration.set_defaults(func=cmd_mark_migration)

    doctor = sub.add_parser("doctor", help="Validate canonical Skills, groups, and recorded links")
    doctor.set_defaults(func=cmd_doctor)

    overlap = sub.add_parser(
        "overlap",
        help="Screen name and description text for possible functional overlap",
        description=(
            "Read-only lexical screening for pairs that require Agent semantic review, "
            "plus dry-run/apply controls for the overlap workflow."
        ),
    )
    overlap_sub = overlap.add_subparsers(dest="overlap_command", required=True)

    overlap_scan = overlap_sub.add_parser(
        "scan",
        help="Screen canonical pairs, or candidates against canonical Skills and each other",
        description=(
            "With no --candidate, screen every canonical pair. Repeat --candidate to screen "
            "one or more Skill directories against canonical Skills and one another. Results "
            "are lexical review candidates, not semantic overlap determinations."
        ),
    )
    overlap_scan.add_argument(
        "--candidate",
        action="append",
        default=[],
        metavar="PATH",
        help="Candidate Skill directory; repeat for batch screening",
    )
    overlap_scan.set_defaults(func=cmd_overlap_scan)

    overlap_set = overlap_sub.add_parser(
        "set", help="Enable or disable overlap scans (dry-run unless --apply)"
    )
    overlap_set.add_argument("setting", choices=("on", "off"), help="Desired scan setting")
    add_apply(overlap_set)
    overlap_set.set_defaults(func=cmd_overlap_set)

    overlap_mark = overlap_sub.add_parser(
        "mark-initial-scan",
        help="Mark the initial overlap scan and semantic review complete",
    )
    add_apply(overlap_mark)
    overlap_mark.set_defaults(func=cmd_overlap_mark_initial_scan)

    group = sub.add_parser("group", help="Manage YAML Skill groups")
    group_sub = group.add_subparsers(dest="group_command", required=True)

    group_list = group_sub.add_parser("list", help="List groups and members")
    group_list.set_defaults(func=cmd_group_list)

    group_show = group_sub.add_parser("show", help="Show one group")
    group_show.add_argument("group")
    group_show.set_defaults(func=cmd_group_show)

    group_create = group_sub.add_parser("create", help="Create an empty group")
    group_create.add_argument("group")
    add_apply(group_create)
    group_create.set_defaults(func=cmd_group_create)

    group_add = group_sub.add_parser("add", help="Add explicit canonical Skill members")
    group_add.add_argument("group")
    group_add.add_argument("skills", nargs="+")
    add_apply(group_add)
    group_add.set_defaults(func=cmd_group_add)

    group_remove = group_sub.add_parser("remove", help="Remove members from a group")
    group_remove.add_argument("group")
    group_remove.add_argument("skills", nargs="+")
    add_apply(group_remove)
    group_remove.set_defaults(func=cmd_group_remove)

    group_delete = group_sub.add_parser("delete", help="Move a group manifest to recoverable backup")
    group_delete.add_argument("group")
    add_apply(group_delete)
    group_delete.set_defaults(func=cmd_group_delete)

    group_rename = group_sub.add_parser("rename", help="Rename a group manifest")
    group_rename.add_argument("group")
    group_rename.add_argument("new_name")
    add_apply(group_rename)
    group_rename.set_defaults(func=cmd_group_rename)

    group_expose = group_sub.add_parser("expose", help="Create flat scoped links for all group members")
    group_expose.add_argument("group")
    add_scope(group_expose)
    add_apply(group_expose)
    group_expose.set_defaults(func=cmd_group_expose)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args(normalize_cli_args(sys.argv[1:]))
    lib = Library(Path(args.library))
    try:
        args.func(args, lib)
    except ManagerError as exc:
        emit({"error": str(exc), "command": args.command})
        return 2
    except OSError as exc:
        emit({"error": f"Filesystem operation failed: {exc}", "command": args.command})
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
