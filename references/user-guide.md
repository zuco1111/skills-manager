# Skill Manager feature guide

## Contents

1. Purpose and boundaries
2. Library layout
3. Installation scopes
4. First-run self-bootstrap and migration
5. Installing and importing Skills
6. Groups
7. Updates, unlinking, removal, and repair
8. Safety behavior
9. Examples
10. Current limitations

## 1. Purpose and boundaries

Skill Manager keeps one canonical copy of each managed Codex Skill and exposes only the Skills relevant to a user or project. It manages standalone local Skills, not plugins or bundled system Skills.

It is responsible for:

- initializing a central Skill library;
- placing or importing completed Skills in that library;
- asking whether every installation is global or project-level;
- creating scoped directory symlinks;
- preinstalling project-level Skills before a project exists;
- optionally migrating existing user-level Skills after consent;
- defining and installing named groups of Skills;
- validating, updating, unlinking, removing, and repairing managed Skills.

It does not author the substantive instructions of a new Skill. Use `skill-creator` for authoring, then use Skill Manager for placement and scope.

Skill Manager is a Skill-only policy. It does not install a lifecycle Hook, so it cannot intercept an installation performed completely outside a Codex conversation or force itself to run immediately after another installer finishes. Within a Skill Manager workflow, the scope question is mandatory.

## 2. Library layout

The default library root is `$HOME/SkillsLibrary`:

```text
SkillsLibrary/
├── skills/
│   ├── skill-manager/
│   ├── fastapi/
│   ├── postgres/
│   └── redis/
├── groups/
│   └── backend.yaml
└── .skill-manager/
    ├── state.json
    └── backups/
```

- `skills/` contains canonical Skill directories. Each directory contains its own `SKILL.md`.
- `groups/` contains YAML manifests that refer to canonical Skill names.
- `.skill-manager/` contains atomic runtime state, staging data, and recoverable backups.

Skill Manager does not initialize Git or ask whether to initialize Git during normal setup. Existing Git metadata is preserved. Git is initialized or modified only on explicit request.

## 3. Installation scopes

Every installation transaction asks for one of two scopes.

### Global

The canonical directory remains in the library. A global link is created at:

```text
$HOME/.agents/skills/<skill-name>
    -> $HOME/SkillsLibrary/skills/<skill-name>
```

### Project-level: bind now

The user supplies a project or module root, for example `/work/payments`. The root must already exist. Skill Manager creates the nested discovery directory when needed:

```text
/work/payments/.agents/skills/<skill-name>
    -> $HOME/SkillsLibrary/skills/<skill-name>
```

The user does not need to create `.agents/skills/` first. A module directory may be supplied as the root, which naturally produces nested scope.

Skill Manager does not distinguish personal and team project installations in version 1.

### Project-level: preinstall before a project exists

A project-level Skill may be installed before its project or module exists. In that case, Skill Manager places and validates the canonical directory under `SkillsLibrary/skills/`, records the user's `project` scope classification, and creates no symlink or separate pending-state field:

```text
$HOME/SkillsLibrary/skills/<skill-name>   # canonical copy exists
<project>/.agents/skills/<skill-name>     # not created yet
```

The missing project directory is not an error in this branch. The Skill is stored for future use but is not yet exposed to any project. When the user later supplies an existing project or module root, Skill Manager creates the normal project link.

### Listing Global and project-level Skills

`SkillsLibrary/skills/` is the complete managed inventory, including Skills that Codex cannot currently discover from the active project. The user-selected classification is stored in `state.json` as `skill_scopes`, with one value per canonical Skill: `global` or `project`. `status` always reports:

- `skills`: every canonical managed Skill;
- `global_skills`: canonical Skills marked `global` by the user;
- `global_exposure_status`: whether each marked-global Skill has the correct global symlink;
- `project_skills`: canonical Skills marked `project` by the user, including project-linked Skills and project Skills installed before a project exists;
- `unclassified_skills`: legacy or incomplete canonical Skills without a scope marker;
- `recorded_project_exposures`: known project roots for project links created by Skill Manager.

The marker—not the presence or absence of a symlink—is the classification source of truth. Symlinks show where a Skill is discoverable and are checked for consistency with that marker. This design needs no separate `pending` or `deferred` field. Recorded project destinations supplement the classification but do not control whether the Skill appears in the inventory.

### Batches

A batch or group asks for scope once and applies it to every member. If project-level is chosen with no available root, all canonical members are installed, validated, and marked `project` in one transaction, with no links or separate pending-state records. Mixed scopes or destinations are handled only when explicitly requested. Every member is preflighted before any mutation.

## 4. First-run self-bootstrap and migration

### Self-bootstrap

The preferred installation puts the canonical Skill Manager directory directly at:

```text
$HOME/SkillsLibrary/skills/skill-manager
```

and creates:

```text
$HOME/.agents/skills/skill-manager
    -> $HOME/SkillsLibrary/skills/skill-manager
```

If a conventional installer places Skill Manager elsewhere, the first invocation can relocate it safely. The bootstrap workflow validates a staged copy, preserves the former installation in a recoverable backup, creates the global link, verifies it, and rolls back on failure.

Because Skill Manager does not use a Hook, this fallback starts on its first invocation rather than immediately after an external installer exits.

### Existing Skill migration

The first invocation asks whether the user wants to scan existing user-level Skills. Nothing is scanned or migrated until the user agrees.

Default discovery is limited to:

```text
$HOME/.agents/skills
${CODEX_HOME:-$HOME/.codex}/skills
```

Project directories are included only when the user explicitly supplies them. Bundled `.system` Skills, administrator Skills, and plugin caches are excluded by default. Skill Manager never searches the entire home directory or filesystem for projects.

A declined onboarding prompt is recorded so it is not repeated automatically. The user can still request migration later.

## 5. Installing and importing Skills

Supported sources include:

- OpenAI curated Skill names through the available `skill-installer` workflow;
- GitHub repository paths or Skill URLs through the available installer;
- local completed Skill directories;
- completed Skills produced by `skill-creator`.

The normal sequence is:

1. Ask global or project-level.
2. For project-level, ask whether a project or module root exists now. Ask for its path only when it does; otherwise install only to the canonical library.
3. Place the completed Skill under `SkillsLibrary/skills/`.
4. Validate `SKILL.md`, `name`, `description`, and directory naming.
5. Show either the intended symlink or the library-only installation plus `project` scope marker.
6. Apply only after confirmation. A successful exposure records its selected scope automatically; a library-only project installation uses `set-scope --scope project`.

If a canonical name already exists, Skill Manager does not overwrite or merge it. It offers the safe choices described in the conflict report.

Installing a single Skill does not trigger a group-membership question. The Skill is added to a group only when the user explicitly requests that action.

## 6. Groups

A group is a named installation bundle represented by a YAML manifest. It is the user-facing name for the reusable grouping behavior sometimes called a profile.

Example `groups/backend.yaml`:

```yaml
name: backend
skills:
  - fastapi
  - postgres
  - redis
```

The same canonical Skill may appear in more than one group. Groups do not copy or move Skill directories.

When a user says:

```text
Install backend skills into /work/payments.
```

Skill Manager resolves `backend`, displays all members, asks or confirms project-level scope and the exact root, preflights every member, creates `.agents/skills/`, and adds one flat symlink per Skill:

```text
/work/payments/.agents/skills/fastapi
/work/payments/.agents/skills/postgres
/work/payments/.agents/skills/redis
```

It never links `backend.yaml` or a group container into `.agents/skills/`.

Group operations include:

- create, list, inspect, rename, and delete a group;
- explicitly add or remove one or more Skill names;
- expose a complete group globally or to a project;
- report missing members and conflicts before installation.

If no project exists yet, the same group request installs or validates every canonical member and marks them `project`, without creating a group link, project directory, or separate pending-state record. Later, the group can be exposed normally when the user supplies a project root.

Version 1 does not support nested groups. If one member is missing or conflicting, group installation stops before creating any new links or canonical copies.

## 7. Updates, unlinking, removal, and repair

### Update

Updates are explicit, never automatic. A replacement Skill is staged and validated, the current canonical copy is moved to a recoverable backup, and the staged version is promoted at the same canonical path. Existing symlinks remain valid.

### Unlink

Unlinking removes only a selected scope symlink. It never removes or follows the canonical target.

### Remove

Removing a canonical Skill is refused while recorded exposures, a live global link, or group memberships remain. This protects the canonical copy even when a correct global link exists but its exposure record is missing. Once clear, removal moves the directory to `.skill-manager/backups/` rather than permanently deleting it.

### Repair

The doctor workflow checks canonical Skill validity, missing or invalid scope markers, contradictions between scope markers and global links, missing or invalid group members, broken or redirected recorded symlinks, and stale exposure records. Repairs require approval and affect only the reported item.

## 8. Safety behavior

- Mutating commands show a dry run unless `--apply` is present.
- Canonical copies are staged and checked against the supported official Skill frontmatter constraints before promotion.
- State and group manifests are written atomically.
- Conflicting paths stop the operation.
- Failed batch linking rolls back links created during that batch.
- A failed state write restores canonical directories, group manifests, or exposure links already moved or removed by that transaction.
- Existing real directories are never treated as removable symlinks.
- Migration and replacement keep recoverable backups.
- Permanent deletion, recursive filesystem discovery, automatic Git initialization, and automatic updates are outside the default workflow.

## 9. Examples

### Install one Skill globally

```text
User: Install the example Skill.
Skill Manager: Should this be global or project-level?
User: Global.
```

### Install one Skill into a project

```text
User: Install the database Skill.
Skill Manager: Should this be global or project-level?
User: Project-level.
Skill Manager: Which existing project or module root should I use?
User: /work/payments
```

### Preinstall one project-level Skill before the project exists

```text
User: Install the database Skill for a future project.
Skill Manager: Should this be global or project-level?
User: Project-level, but I do not have the project directory yet.
Skill Manager: I will place it in SkillsLibrary, mark it project-level, and create no project symlink. It will remain in the complete Skill inventory and can be linked later.
```

### Add a Skill to a group

```text
User: Add redis to the backend group.
```

No group-membership question is asked during unrelated installations.

### Install a group

```text
User: Install backend skills into my payments project.
Skill Manager: The backend group contains fastapi, postgres, and redis. Confirm project-level installation and provide the project root.
```

If the project does not exist yet, the user can say so; all three canonical Skills are installed into the library, marked `project`, and left without links or separate pending-state records.

## 10. Current limitations

- No Hook-based interception of external installation flows.
- No plugin lifecycle management.
- No nested groups.
- No automatic update scheduler.
- No automatic Git initialization.
- No silent copy fallback when directory symlinks are unavailable.
- No distinction between personal and team project scopes in version 1.
