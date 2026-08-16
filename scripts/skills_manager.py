#!/usr/bin/env python3
"""Deterministic filesystem operations for the skills-manager Skill.

Mutating commands are dry-runs unless --apply is present. The script deliberately
does not decide installation scope or prompt users; SKILL.md owns those choices.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_FRONTMATTER_KEYS = {"name", "description", "license", "allowed-tools", "metadata"}
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
DEFAULT_LIBRARY = Path.home() / "SkillsLibrary"
GLOBAL_SKILLS_DIR = Path.home() / ".agents" / "skills"


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
            "migration_status": "not-asked",
            "exposures": {},
            "skill_scopes": {},
            "backups": [],
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
        state.setdefault("skill_scopes", {})
        state.setdefault("backups", [])
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


def scope_link(name: str, scope: str, project: str | None) -> tuple[Path, Path | None]:
    validate_identifier(name, "Skill name")
    if scope == "global":
        if project:
            fail("--project is not valid with global scope")
        return GLOBAL_SKILLS_DIR / name, None
    if not project:
        fail("Project scope requires --project with an existing project or module root")
    project_root = lexical_path(project)
    if not project_root.is_dir():
        fail(f"Project or module root does not exist: {project_root}")
    return project_root / ".agents" / "skills" / name, project_root


def link_status(link: Path, target: Path) -> str:
    if not lexists(link):
        return "create"
    if link.is_symlink() and resolved(link) == resolved(target):
        return "already-correct"
    if link.is_symlink():
        return f"conflict-symlink:{resolved(link)}"
    return "conflict-existing-path"


def ensure_scope_compatible(name: str, scope: str, target: Path) -> None:
    if scope != "project":
        return
    global_status = link_status(GLOBAL_SKILLS_DIR / name, target)
    if global_status == "already-correct":
        fail(
            f"Skill {name!r} is globally exposed; unexpose it globally before "
            "classifying or exposing it as project-level"
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
    state: dict[str, Any], link: Path, target: Path, skill: str, scope: str, project: Path | None
) -> None:
    state["exposures"][str(link)] = {
        "skill": skill,
        "target": str(target),
        "scope": scope,
        "project": str(project) if project else None,
    }


def apply_exposures(
    lib: Library,
    items: list[dict[str, Any]],
    scope: str,
    project_root: Path | None,
) -> list[str]:
    state = lib.load_state()
    created: list[Path] = []
    try:
        for item in items:
            state["skill_scopes"][item["skill"]] = scope
            if item["status"] == "already-correct":
                record_exposure(
                    state, item["link"], item["target"], item["skill"], scope, project_root
                )
                continue
            item["link"].parent.mkdir(parents=True, exist_ok=True)
            os.symlink(item["target"], item["link"], target_is_directory=True)
            created.append(item["link"])
            if resolved(item["link"]) != resolved(item["target"]):
                fail(f"Created link did not resolve to target: {item['link']}")
            record_exposure(
                state, item["link"], item["target"], item["skill"], scope, project_root
            )
        lib.save_state(state)
    except Exception:
        for path in reversed(created):
            if path.is_symlink():
                path.unlink()
        raise
    return [str(path) for path in created]


def plan_exposures(
    lib: Library, names: Iterable[str], scope: str, project: str | None
) -> tuple[list[dict[str, Any]], Path | None]:
    items: list[dict[str, Any]] = []
    project_root: Path | None = None
    for name in names:
        target = lib.skill_path(name)
        result = validate_skill(target)
        if not result["valid"]:
            fail(f"Cannot expose invalid or missing Skill {name!r}: {result['errors']}")
        ensure_scope_compatible(name, scope, target)
        link, this_project = scope_link(name, scope, project)
        if project_root is None:
            project_root = this_project
        status = link_status(link, target)
        if status.startswith("conflict"):
            fail(f"Exposure conflict at {link}: {status}")
        items.append({"skill": name, "target": target, "link": link, "status": status})
    return items, project_root


def copy_skill_to_stage(lib: Library, source: Path, name: str) -> Path:
    stage = lib.stage_path(name)
    shutil.copytree(source, stage, symlinks=True)
    result = validate_skill(stage, require_dir_name=False)
    if not result["valid"] or result["name"] != name:
        shutil.rmtree(stage, ignore_errors=True)
        fail(f"Staged Skill validation failed: {result['errors']}")
    return stage


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
    skills = list_canonical_skills(lib)
    global_skills = [name for name in skills if state["skill_scopes"].get(name) == "global"]
    project_skills = [name for name in skills if state["skill_scopes"].get(name) == "project"]
    unclassified_skills = [
        name for name in skills if state["skill_scopes"].get(name) not in {"global", "project"}
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
    emit(
        {
            "library": str(lib.root),
            "library_exists": lib.root.is_dir(),
            "canonical_manager": str(canonical),
            "canonical_manager_valid": validate_skill(canonical)["valid"] if canonical.exists() else False,
            "global_manager_link": str(global_link),
            "global_manager_link_status": link_status(global_link, canonical),
            "migration_status": state["migration_status"],
            "skills": skills,
            "global_skills": global_skills,
            "global_exposure_status": global_exposure_status,
            "project_skills": project_skills,
            "unclassified_skills": unclassified_skills,
            "recorded_project_exposures": recorded_project_exposures,
            "groups": list_groups(lib),
            "recorded_exposures": len(state["exposures"]),
            "recoverable_backups": len(state["backups"]),
        }
    )


def cmd_validate(args: argparse.Namespace, lib: Library) -> None:
    result = validate_skill(lexical_path(args.skill_dir))
    emit(result)
    if not result["valid"]:
        raise SystemExit(1)


def cmd_discover(args: argparse.Namespace, lib: Library) -> None:
    roots: list[tuple[str, Path]] = [("user", GLOBAL_SKILLS_DIR)]
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    roots.append(("user-compat", lexical_path(codex_home) / "skills"))
    for project in args.project or []:
        root = lexical_path(project)
        if not root.is_dir():
            fail(f"Project or module root does not exist: {root}")
        roots.append(("project", root / ".agents" / "skills"))

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for scope, root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda item: item.name):
            if child.name.startswith(".") or child.name == ".system":
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
    emit({"roots": [{"scope": scope, "path": str(path)} for scope, path in roots], "candidates": candidates})


def cmd_adopt(args: argparse.Namespace, lib: Library) -> None:
    source = resolved(lexical_path(args.source))
    validation = validate_skill(source, require_dir_name=False)
    if not validation["valid"]:
        fail(f"Source Skill is invalid: {validation['errors']}")
    name = validate_identifier(str(validation["name"]), "Skill name")
    target = lib.skill_path(name)
    if target.exists() and resolved(target) == source:
        emit({"action": "adopt", "result": "already-canonical", "skill": name, "target": str(target)})
        return
    if target.exists() and not args.replace:
        fail(f"Canonical Skill already exists at {target}; use --replace only after explicit confirmation")

    plan = {
        "action": "replace" if target.exists() else "adopt",
        "apply": args.apply,
        "skill": name,
        "source": str(source),
        "target": str(target),
        "existing_target": target.exists(),
    }
    if not args.apply:
        emit(plan)
        return

    lib.ensure_layout()
    stage = copy_skill_to_stage(lib, source, name)
    backup: Path | None = None
    try:
        if target.exists():
            backup = lib.backup_path(name, "replace")
            shutil.move(str(target), str(backup))
        os.replace(stage, target)
        result = validate_skill(target)
        if not result["valid"]:
            fail(f"Promoted Skill failed validation: {result['errors']}")
        state = lib.load_state()
        if backup:
            record_backup(state, backup, target, "replace")
        lib.save_state(state)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        if target.exists() and backup and backup.exists():
            shutil.rmtree(target, ignore_errors=True)
        if backup and backup.exists() and not target.exists():
            shutil.move(str(backup), str(target))
        raise
    plan["backup"] = str(backup) if backup else None
    emit(plan)


def cmd_expose(args: argparse.Namespace, lib: Library) -> None:
    items, project_root = plan_exposures(lib, [args.skill], args.scope, args.project)
    payload = {
        "action": "expose",
        "apply": args.apply,
        "scope": args.scope,
        "project": str(project_root) if project_root else None,
        "links": [
            {"skill": item["skill"], "link": str(item["link"]), "target": str(item["target"]), "status": item["status"]}
            for item in items
        ],
    }
    if args.apply:
        lib.ensure_layout()
        payload["created"] = apply_exposures(lib, items, args.scope, project_root)
    emit(payload)


def cmd_set_scope(args: argparse.Namespace, lib: Library) -> None:
    names = list(dict.fromkeys(validate_identifier(name, "Skill name") for name in args.skills))
    state = lib.load_state()
    items: list[dict[str, Any]] = []
    for name in names:
        target = lib.skill_path(name)
        result = validate_skill(target)
        if not result["valid"]:
            fail(f"Cannot classify invalid or missing Skill {name!r}: {result['errors']}")
        global_status = link_status(GLOBAL_SKILLS_DIR / name, target)
        if args.scope == "global" and global_status != "already-correct":
            fail(
                f"Cannot mark {name!r} global without a correct global link; "
                "use expose --scope global"
            )
        ensure_scope_compatible(name, args.scope, target)
        items.append(
            {
                "skill": name,
                "target": str(target),
                "previous_scope": state["skill_scopes"].get(name),
                "new_scope": args.scope,
            }
        )

    payload: dict[str, Any] = {
        "action": "set-scope",
        "apply": args.apply,
        "scope": args.scope,
        "skills": items,
    }
    if args.apply:
        for name in names:
            state["skill_scopes"][name] = args.scope
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
    ensure_scope_compatible(name, scope, target)
    link, project_root = scope_link(name, scope, project)
    if resolved(source) == resolved(target):
        items, project_root = plan_exposures(lib, [name], scope, project)
        payload = {
            "action": "migrate",
            "result": "already-canonical",
            "apply": apply,
            "skill": name,
            "source": str(source),
            "target": str(target),
            "link": str(items[0]["link"]),
        }
        if apply:
            lib.ensure_layout()
            payload["created"] = apply_exposures(lib, items, scope, project_root)
        return payload
    if target.exists():
        fail(f"Canonical destination already exists: {target}")
    status = link_status(link, target)
    source_is_link_path = source == link and source.is_dir() and not source.is_symlink()
    if status.startswith("conflict") and not source_is_link_path:
        fail(f"Exposure conflict at {link}: {status}")

    payload: dict[str, Any] = {
        "action": "migrate",
        "apply": apply,
        "skill": name,
        "source": str(source),
        "target": str(target),
        "scope": scope,
        "project": str(project_root) if project_root else None,
        "link": str(link),
        "source_becomes_backup": True,
    }
    if not apply:
        return payload

    lib.ensure_layout()
    stage = copy_skill_to_stage(lib, resolved(source), name)
    backup = lib.backup_path(name, "migrate")
    link_created = False
    target_created = False
    source_moved = False
    try:
        os.replace(stage, target)
        target_created = True
        shutil.move(str(source), str(backup))
        source_moved = True
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, link, target_is_directory=True)
        link_created = True
        if resolved(link) != resolved(target):
            fail(f"Created link did not resolve to target: {link}")
        state = lib.load_state()
        record_backup(state, backup, source, "migrate")
        record_exposure(state, link, target, name, scope, project_root)
        state["skill_scopes"][name] = scope
        lib.save_state(state)
    except Exception:
        if link_created and link.is_symlink():
            link.unlink()
        if source_moved and backup.exists() and not source.exists():
            shutil.move(str(backup), str(source))
        if target_created and target.exists():
            shutil.rmtree(target, ignore_errors=True)
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        raise
    payload["backup"] = str(backup)
    return payload


def cmd_migrate(args: argparse.Namespace, lib: Library) -> None:
    emit(migrate_core(lib, lexical_path(args.source), args.scope, args.project, args.apply))


def cmd_bootstrap(args: argparse.Namespace, lib: Library) -> None:
    source = lexical_path(args.source) if args.source else lexical_path(Path(__file__).parent.parent)
    payload = migrate_core(lib, source, "global", None, args.apply, expected_name="skills-manager")
    payload["action"] = "bootstrap"
    if args.apply:
        payload["next_step"] = "Use the canonical Skill on the next turn; restart Codex if it does not appear."
    emit(payload)


def cmd_unexpose(args: argparse.Namespace, lib: Library) -> None:
    target = lib.skill_path(args.skill)
    link, project_root = scope_link(args.skill, args.scope, args.project)
    state = lib.load_state()
    recorded = str(link) in state["exposures"]
    if not lexists(link):
        payload = {
            "action": "unexpose",
            "apply": args.apply,
            "skill": args.skill,
            "link": str(link),
            "result": "missing-link",
            "clear_stale_record": recorded,
            "scope_marker": state["skill_scopes"].get(args.skill),
        }
        if args.apply and recorded:
            state["exposures"].pop(str(link), None)
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
        "scope": args.scope,
        "project": str(project_root) if project_root else None,
        "link": str(link),
        "target": str(target),
        "scope_marker": state["skill_scopes"].get(args.skill),
    }
    if args.apply:
        link_removed = False
        try:
            link.unlink()
            link_removed = True
            state["exposures"].pop(str(link), None)
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
    memberships = group_memberships(lib, name)
    if exposure_paths:
        fail(f"Remove active or recorded exposures first: {sorted(exposure_paths)}")
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
    items, project_root = plan_exposures(lib, manifest["skills"], args.scope, args.project)
    payload = {
        "action": "group-expose",
        "apply": args.apply,
        "group": args.group,
        "scope": args.scope,
        "project": str(project_root) if project_root else None,
        "links": [
            {"skill": item["skill"], "link": str(item["link"]), "target": str(item["target"]), "status": item["status"]}
            for item in items
        ],
    }
    if args.apply:
        lib.ensure_layout()
        payload["created"] = apply_exposures(lib, items, args.scope, project_root)
    emit(payload)


def cmd_doctor(args: argparse.Namespace, lib: Library) -> None:
    issues: list[dict[str, str]] = []
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
    for skill in sorted(canonical_names - set(state["skill_scopes"])):
        issues.append({"type": "unclassified-skill", "path": str(lib.skill_path(skill))})
    for link_text, item in state["exposures"].items():
        link = Path(link_text)
        target = Path(item["target"])
        if not lexists(link):
            issues.append({"type": "missing-recorded-link", "path": link_text})
        elif not link.is_symlink():
            issues.append({"type": "recorded-link-is-not-symlink", "path": link_text})
        elif resolved(link) != resolved(target):
            issues.append({"type": "redirected-link", "path": link_text, "detail": str(resolved(link))})
    emit({"healthy": not issues, "issues": issues})
    if issues:
        raise SystemExit(1)


def add_apply(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--apply", action="store_true", help="Apply the displayed mutation")


def add_scope(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scope", choices=("global", "project"), required=True)
    parser.add_argument("--project", help="Existing project or module root for project scope")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", default=str(DEFAULT_LIBRARY), help="Central library root")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Initialize the central library without Git")
    add_apply(init)
    init.set_defaults(func=cmd_init)

    status = sub.add_parser("status", help="Show library, onboarding, and self-bootstrap status")
    status.set_defaults(func=cmd_status)

    validate = sub.add_parser("validate", help="Validate one Skill directory")
    validate.add_argument("skill_dir")
    validate.set_defaults(func=cmd_validate)

    discover = sub.add_parser("discover", help="Discover migration candidates in approved roots")
    discover.add_argument("--project", action="append", help="Additional project or module root")
    discover.set_defaults(func=cmd_discover)

    adopt = sub.add_parser("adopt", help="Copy a completed local Skill into the library")
    adopt.add_argument("source")
    adopt.add_argument("--replace", action="store_true", help="Replace an existing canonical copy with backup")
    add_apply(adopt)
    adopt.set_defaults(func=cmd_adopt)

    expose = sub.add_parser("expose", help="Create one scoped Skill symlink")
    expose.add_argument("skill")
    add_scope(expose)
    add_apply(expose)
    expose.set_defaults(func=cmd_expose)

    set_scope = sub.add_parser("set-scope", help="Record the user-selected Skill classification")
    set_scope.add_argument("skills", nargs="+")
    set_scope.add_argument("--scope", choices=("global", "project"), required=True)
    add_apply(set_scope)
    set_scope.set_defaults(func=cmd_set_scope)

    migrate = sub.add_parser("migrate", help="Move one real Skill directory into the library and expose it")
    migrate.add_argument("source")
    add_scope(migrate)
    add_apply(migrate)
    migrate.set_defaults(func=cmd_migrate)

    bootstrap = sub.add_parser("bootstrap", help="Relocate and globally link skills-manager itself")
    bootstrap.add_argument("--source", help="Currently active skills-manager directory")
    add_apply(bootstrap)
    bootstrap.set_defaults(func=cmd_bootstrap)

    unexpose = sub.add_parser("unexpose", help="Remove only one managed scope symlink")
    unexpose.add_argument("skill")
    add_scope(unexpose)
    add_apply(unexpose)
    unexpose.set_defaults(func=cmd_unexpose)

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
    args = parser.parse_args()
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
