# Skills Manager feature guide

## Contents

1. Purpose and boundaries
2. Library layout
3. Installation scopes
4. First-run initialization and migration
5. Installing and importing Skills
6. Functional-overlap checks
7. Groups
8. Updates, unlinking, removal, and repair
9. Safety behavior
10. Examples
11. Current limitations

## 1. Purpose and boundaries

Skills Manager is a general-purpose manager for standalone agent Skills across compatible Skill-based environments. It keeps one canonical copy of each managed Skill and exposes only the Skills relevant to a user or project. It does not manage plugins or bundled system Skills.

It is responsible for:

- initializing a central Skill library;
- placing or importing completed Skills in that library;
- asking whether every installation is global or project-level;
- creating scoped directory symlinks;
- preinstalling project-level Skills before a project exists;
- optionally migrating existing user-level Skills after consent;
- checking staged and managed Skills for functional overlap that could cause ambiguous routing;
- defining and installing named groups of Skills;
- validating, updating, unlinking, removing, and repairing managed Skills.

It does not author the substantive instructions of a new Skill. Use `skill-creator` for authoring, then use Skills Manager for placement and scope.

Skills Manager is a Skill-only policy and does not install a lifecycle Hook. An available `skill-installer` workflow can give a brief initialization notice after installing Skills Manager. Independently, the first Skills Manager invocation checks the existing canonical-copy and global-link status fields and explicitly tells the user to initialize before use when either check fails. Within a Skills Manager workflow, the scope question is mandatory.

## 2. Library layout

The default library root is `$HOME/SkillsLibrary`:

```text
SkillsLibrary/
├── skills/
│   ├── skills-manager/
│   ├── fastapi/
│   ├── postgres/
│   └── redis/
├── groups/
│   └── backend.yaml
└── .skills-manager/
    ├── state.json
    └── backups/
```

- `skills/` contains canonical Skill directories. Each directory contains its own `SKILL.md`.
- `groups/` contains YAML manifests that refer to canonical Skill names.
- `.skills-manager/` contains atomic runtime state, staging data, recoverable backups, and the overlap-check preference and initial-scan marker.

Skills Manager does not initialize Git or ask whether to initialize Git during normal setup. Existing Git metadata is preserved. Git is initialized or modified only on explicit request.

## 3. Installation scopes

Every installation transaction asks for one of two scopes.

### Global

The canonical directory remains in the library. A global link is created at:

```text
$HOME/.agents/skills/<skill-name>
    -> $HOME/SkillsLibrary/skills/<skill-name>
```

### Project-level: bind now

The user supplies a project or module root, for example `/work/payments`. The root must already exist. Skills Manager creates the nested discovery directory when needed:

```text
/work/payments/.agents/skills/<skill-name>
    -> $HOME/SkillsLibrary/skills/<skill-name>
```

The user does not need to create `.agents/skills/` first. A module directory may be supplied as the root, which naturally produces nested scope.

Skills Manager does not distinguish personal and team project installations in version 1.

### Project-level: preinstall before a project exists

A project-level Skill may be installed before its project or module exists. In that case, Skills Manager places and validates the canonical directory under `SkillsLibrary/skills/`, records the user's `project` scope classification, and creates no symlink or separate pending-state field:

```text
$HOME/SkillsLibrary/skills/<skill-name>   # canonical copy exists
<project>/.agents/skills/<skill-name>     # not created yet
```

The missing project directory is not an error in this branch. The Skill is stored for future use but is not yet exposed to any project. When the user later supplies an existing project or module root, Skills Manager creates the normal project link.

### Listing Global and project-level Skills

`SkillsLibrary/skills/` is the complete managed inventory, including Skills that the active agent cannot currently discover from the current project. The user-selected classification is stored in `state.json` as `skill_scopes`, with one value per canonical Skill: `global` or `project`. `status` always reports:

- `skills`: every canonical managed Skill;
- `global_skills`: canonical Skills marked `global` by the user;
- `global_exposure_status`: whether each marked-global Skill has the correct global symlink;
- `project_skills`: canonical Skills marked `project` by the user, including project-linked Skills and project Skills installed before a project exists;
- `unclassified_skills`: legacy or incomplete canonical Skills without a scope marker;
- `recorded_project_exposures`: known project roots for project links created by Skills Manager.

The marker—not the presence or absence of a symlink—is the classification source of truth. Symlinks show where a Skill is discoverable and are checked for consistency with that marker. This design needs no separate `pending` or `deferred` field. Recorded project destinations supplement the classification but do not control whether the Skill appears in the inventory.

### Batches

A batch or group asks for scope once and applies it to every member. If project-level is chosen with no available root, all canonical members are installed, validated, and marked `project` in one transaction, with no links or separate pending-state records. Mixed scopes or destinations are handled only when explicitly requested. Every member is preflighted before any mutation.

## 4. First-run initialization and migration

### Initialization

The preferred installation puts the canonical Skills Manager directory directly at:

```text
$HOME/SkillsLibrary/skills/skills-manager
```

and creates:

```text
$HOME/.agents/skills/skills-manager
    -> $HOME/SkillsLibrary/skills/skills-manager
```

If a conventional installer places Skills Manager elsewhere, the first invocation clearly reports that the Skill must be initialized before use and offers to do so. The initialization workflow validates a staged copy, preserves the former installation in a recoverable backup, creates the global link, verifies it, and rolls back on failure.

Because Skills Manager does not use a Hook, the installation-time notice comes from the installing Agent's completion message. Initialization itself starts on the first Skills Manager invocation, which checks the canonical copy and global link again before doing other work.

After initialization, Skills Manager performs one functional-overlap scan across the canonical inventory unless the user disabled the check. The script reports broad candidates; the Agent completes the semantic review before recording the initial scan as done. Existing libraries upgraded to this behavior receive the same one-time scan.

### Existing Skill migration

After initialization, the first invocation asks whether the user wants to scan existing user-level Skills. Nothing is scanned or migrated until the user agrees.

Default discovery is limited to:

```text
$HOME/.agents/skills
${CODEX_HOME:-$HOME/.codex}/skills
```

Project directories are included only when the user explicitly supplies them. Bundled `.system` Skills, administrator Skills, and plugin caches are excluded by default. Skills Manager never searches the entire home directory or filesystem for projects.

A declined onboarding prompt is recorded so it is not repeated automatically. The user can still request migration later.

Once the user agrees to migrate, selects the candidates, and provides each required scope and project destination, Skills Manager dry-runs every selected item. It immediately applies every conflict-free item without asking the user to confirm the same plan again. If some items conflict, the conflict-free items still migrate; only the conflicting items and their safe resolution choices are sent back for confirmation. Resolving one conflict does not trigger reconfirmation of the complete migration set.

## 5. Installing and importing Skills

Supported sources include:

- OpenAI curated Skill names through the available `skill-installer` workflow;
- GitHub repository paths or Skill URLs through the available installer;
- local completed Skill directories;
- completed Skills produced by `skill-creator`.

The normal sequence is:

1. Ask global or project-level.
2. For project-level, ask whether a project or module root exists now. Ask for its path only when it does; otherwise install only to the canonical library.
3. Stage a remote Skill outside the canonical destination, or identify the completed local, authored, import, or migration directory.
4. Validate `SKILL.md`, `name`, `description`, and directory naming.
5. Run the overlap candidate scan and let the Agent complete the semantic review before adopting, promoting, migrating, or exposing the Skill.
6. Place the approved Skill under `SkillsLibrary/skills/`.
7. Show either the intended symlink or the library-only installation plus `project` scope marker.
8. Apply only after confirmation. A successful exposure records its selected scope automatically; a library-only project installation uses `set-scope --scope project`.

If a canonical name already exists, Skills Manager does not overwrite or merge it. It offers the safe choices described in the conflict report.

Installing a single Skill does not trigger a group-membership question. The Skill is added to a group only when the user explicitly requests that action.

For a batch, pass every staged or local candidate to one scan and review all reported pairs together. Apply the same check to selected migration/import directories before the first mutation.

## 6. Functional-overlap checks

Functional overlap is enabled by default. It detects likely routing conflicts, not byte-for-byte copies or similar folder contents.

Run the one-time library scan with:

```bash
python3 scripts/skills_manager.py overlap scan
```

Scan one or more staged or local candidates with repeated paths:

```bash
python3 scripts/skills_manager.py overlap scan \
  --candidate <first-skill-directory> \
  --candidate <second-skill-directory>
```

Treat the script output only as a broad lexical filter. Prioritize `lexical_candidates`, then review the `scanned_items` names and descriptions for obvious semantic equivalents that share few literal words. Compare only plausible same-object items rather than reading every body. Read the two `SKILL.md` bodies only when their metadata does not establish the boundary; do not inspect scripts, references, or assets for this decision.

Classify a pair as highly overlapping only when all of these are true:

- the Skills operate on the same object;
- their core actions overlap or one Skill contains the other's capability;
- common user requests could reasonably trigger both;
- no clear routing boundary separates them.

Flag a capability subset because it can still cause ambiguous routing. Do not flag Skills merely for sharing a domain when their core actions differ. For example, a Skill that sends email and a Skill that manages email account settings share a domain but have a clear action boundary.

Examples:

- a PPT beautification Skill and a PPT optimization Skill are highly overlapping when both answer requests such as "make this presentation look better";
- a general email-management Skill and a send-email Skill are highly overlapping when the broader Skill includes sending mail;
- a send-email Skill and an email-account-settings Skill are not highly overlapping because their core actions and routing boundaries differ.

When no high overlap remains after semantic review, continue without adding an overlap confirmation. When high overlap exists, present all affected pairs in one decision request. Include both descriptions, current scopes, exposures, group memberships, overlap, differences, and the routing risk. Offer four choices for each affected candidate:

- **Keep both:** install the new Skill and keep both routable; do not merge them.
- **Keep existing:** skip the new candidate and continue unaffected batch items.
- **Keep new:** first adopt, validate, and expose the candidate at the user-selected scope. Then remove the existing Skill from groups, remove its exposures, and use `remove` so its canonical copy moves to a recoverable backup. For a project preinstallation without an existing root, keep the existing Skill active and defer its retirement until the candidate can be exposed in a supplied project root. If cleanup fails, the new Skill remains active; report the exact old dependencies or canonical paths that remain and offer the normal repair workflow. This is a safe availability order, not an atomic switchover.
- **Cancel:** stop the affected install or import without changing either Skill.

Never merge, delete, or replace a Skill only because the script returned it as a candidate. Treat same-name collisions through the normal collision workflow and same-target replacements through the update workflow.

After the initial semantic review, record completion with `overlap mark-initial-scan`. Run its dry run first; the completed review authorizes applying this bookkeeping update without a separate overlap prompt when no high overlap exists. If high overlap exists, include the marker update in the consolidated decision plan. Change the preference only on explicit request by dry-running and then applying `overlap set on|off`.

The standard Agent installation workflow uses staging, but direct low-level installer-script calls, external installers, and filesystem copies can bypass this workflow. Skills Manager has no Hook that can intercept them. When an externally placed Skill is later discovered, run a manual canonical scan; do not claim that the earlier installation was checked.

## 7. Groups

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

Skills Manager resolves `backend`, displays all members, asks or confirms project-level scope and the exact root, preflights every member, creates `.agents/skills/`, and adds one flat symlink per Skill:

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

## 8. Updates, unlinking, removal, and repair

### Update

Updates are explicit, never automatic. A replacement Skill is staged and validated, the current canonical copy is moved to a recoverable backup, and the staged version is promoted at the same canonical path. Existing symlinks remain valid.

### Unlink

Unlinking removes only a selected scope symlink. It never removes or follows the canonical target.

### Remove

Removing a canonical Skill is refused while recorded exposures, a live global link, or group memberships remain. This protects the canonical copy even when a correct global link exists but its exposure record is missing. Once clear, removal moves the directory to `.skills-manager/backups/` rather than permanently deleting it.

### Repair

The doctor workflow checks canonical Skill validity, missing or invalid scope markers, contradictions between scope markers and global links, missing or invalid group members, broken or redirected recorded symlinks, and stale exposure records. Repairs require approval and affect only the reported item.

## 9. Safety behavior

- Mutating commands show a dry run unless `--apply` is present. Prior migration consent authorizes conflict-free migration applications, and a completed initial overlap review authorizes its bookkeeping marker; other operations retain their explicit plan-confirmation step.
- Canonical copies are staged and checked against the supported official Skill frontmatter constraints before promotion.
- State and group manifests are written atomically.
- Conflicting paths stop the operation.
- Failed batch linking rolls back links created during that batch.
- A failed state write restores canonical directories, group manifests, or exposure links already moved or removed by that transaction.
- Existing real directories are never treated as removable symlinks.
- Migration and replacement keep recoverable backups.
- Permanent deletion, recursive filesystem discovery, automatic Git initialization, and automatic updates are outside the default workflow.

## 10. Examples

### Install one Skill globally

```text
User: Install the example Skill.
Skills Manager: Should this be global or project-level?
User: Global.
```

### Install one Skill into a project

```text
User: Install the database Skill.
Skills Manager: Should this be global or project-level?
User: Project-level.
Skills Manager: Which existing project or module root should I use?
User: /work/payments
```

### Preinstall one project-level Skill before the project exists

```text
User: Install the database Skill for a future project.
Skills Manager: Should this be global or project-level?
User: Project-level, but I do not have the project directory yet.
Skills Manager: I will place it in SkillsLibrary, mark it project-level, and create no project symlink. It will remain in the complete Skill inventory and can be linked later.
```

### Add a Skill to a group

```text
User: Add redis to the backend group.
```

No group-membership question is asked during unrelated installations.

### Install a group

```text
User: Install backend skills into my payments project.
Skills Manager: The backend group contains fastapi, postgres, and redis. Confirm project-level installation and provide the project root.
```

If the project does not exist yet, the user can say so; all three canonical Skills are installed into the library, marked `project`, and left without links or separate pending-state records.

## 11. Current limitations

- No Hook-based interception of external installation or overlap-check bypasses.
- No plugin lifecycle management.
- No nested groups.
- No automatic update scheduler.
- No automatic Git initialization.
- No silent copy fallback when directory symlinks are unavailable.
- No distinction between personal and team project scopes in version 1.
