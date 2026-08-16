---
name: skill-manager
description: Install and manage standalone Codex Skills—not plugins or bundled/system/plugin-cache Skills—through a central SkillsLibrary with mandatory global-or-project scope selection. Use when users ask to install, import, migrate, list, classify, group, update, repair, expose, unlink, or remove Skills; preinstall project Skills before a project exists; install named Skill groups such as "backend skills"; or ask about Skill Manager features and documentation. Store canonical copies in SkillsLibrary and expose them with symlinks. Use skill-creator, not this Skill, to author Skill content.
---

# Skill Manager

Manage canonical Skill copies in a central library and expose them through scoped directory symlinks. Keep the workflow interactive at decision points and deterministic for filesystem operations.

## Non-negotiable rules

- Keep canonical Skill directories under `$HOME/SkillsLibrary/skills/` unless the user explicitly configured another library root.
- Expose global Skills through `$HOME/.agents/skills/<skill-name>`.
- Expose project or module Skills through `<user-provided-root>/.agents/skills/<skill-name>`.
- Before every single-Skill or group installation, ask whether it is **global** or **project-level**. A batch or group needs one scope question for the whole transaction unless the user requests mixed scopes.
- After project-level is selected, ask whether an existing project or module root is available now. If it is, accept the root directory, create `.agents/skills/` beneath it, and never require the user to provide that nested directory. If it is not, keep the canonical copy in the library, mark its user-selected scope as `project`, and create no symlink. Do not add a separate pending or binding marker.
- Do not infer a project path. When linking now, require an existing directory and confirm the exact normalized path before mutation. A missing project path is valid for a project-level preinstallation that only writes the canonical library copy.
- Do not ask whether to add a newly installed Skill to a group. Add group membership only when the user explicitly requests it.
- Never initialize Git automatically and never ask about Git during normal onboarding. Only initialize or modify Git when explicitly requested.
- Never overwrite a real directory, conflicting symlink, canonical Skill, or group manifest without showing the conflict and receiving explicit confirmation for the selected resolution.
- Exclude bundled system Skills, administrator Skills, and plugin-managed cache directories from migration by default.
- Prefer dry-run output before every mutating script command. Run the same command with `--apply` only after the user confirms the exact plan.

## Load the feature guide when needed

Read [references/user-guide.md](references/user-guide.md) completely when the user asks what this Skill supports, requests documentation, asks how groups or migration work, or needs examples. Answer in the user's language.

## Start every workflow

1. Resolve this Skill's actual directory from the active Skill path.
2. Run `python3 scripts/skill_manager.py status`.
3. If the canonical library or global `skill-manager` link is missing, follow **Self-bootstrap** before managing other Skills.
4. If `migration_status` is `not-asked`, ask whether the user wants to scan existing user-level Skills for migration. Record the answer with `mark-migration`; do not scan before consent.
5. Identify the requested operation and follow the relevant workflow below.

## Self-bootstrap

Use self-bootstrap when this Skill was installed outside the canonical library.

1. Explain that the real copy will live at `$HOME/SkillsLibrary/skills/skill-manager` and the global entry will be a symlink.
2. Show a dry run, passing the currently active Skill folder explicitly:

   ```bash
   python3 scripts/skill_manager.py bootstrap --source <active-skill-folder>
   ```

3. After confirmation, rerun with `--apply`.
4. Report the canonical path, symlink path, and any recoverable backup path.
5. Tell the user the relocated Skill will be used on the next turn; if it does not appear, restart Codex.

The bootstrap command must be idempotent. It must validate the copied Skill before switching paths and roll back on failure.

## Install or import one Skill

1. Ask the mandatory scope question: global or project-level.
2. For project-level, ask whether a project or module root exists now. If yes, ask for and confirm it. If no, confirm that only the canonical library copy will be installed and no link will be created yet.
3. Resolve the source:
   - For an OpenAI curated Skill or GitHub source, use the available `skill-installer` workflow and pass the library's `skills/` directory as its destination. Do not let it default to another user directory.
   - For a local completed Skill, run `adopt <source>` as a dry run, then `adopt <source> --apply` after confirmation.
   - For a newly authored Skill, let `skill-creator` finish and validate its contents first, then adopt it here.
4. Validate the canonical directory:

   ```bash
   python3 scripts/skill_manager.py validate <canonical-skill-directory>
   ```

5. If a destination exists, show the exposure plan:

   ```bash
   python3 scripts/skill_manager.py expose <skill-name> --scope global
   python3 scripts/skill_manager.py expose <skill-name> --scope project --project <root>
   ```

6. After confirmation, repeat the applicable exposure command with `--apply`; exposure records the selected scope. If the project does not exist yet, skip exposure and record the user's classification instead:

   ```bash
   python3 scripts/skill_manager.py set-scope <skill-name> --scope project
   python3 scripts/skill_manager.py set-scope <skill-name> --scope project --apply
   ```

   The installation is complete once the canonical copy is valid and its scope is marked.
7. A project-level Skill without a project link is intentionally undiscoverable by Codex until the user later supplies a root; then use `expose --scope project --project <root>`.
8. Do not ask about group membership unless the user already requested a group operation.

If installation produces an invalid Skill, leave it unexposed and report the validation errors.

## Install multiple Skills

Ask for scope once for the batch. For project-level, ask whether one root exists now unless the user requests mixed destinations. If not, install and validate all canonical members, then mark the entire batch `project` with one `set-scope <skill>... --scope project` transaction without creating links. When a root is supplied, preflight every Skill and link before applying changes. If any member has a conflict, stop the batch rather than leaving a partial installation.

## List managed Skills

When the user asks which Skills are installed or available, list the complete canonical inventory from `SkillsLibrary/skills/`, not only the Skills Codex can currently discover. Run `status` and present both classifications:

- **Global Skills:** canonical Skills carrying the user-selected `global` scope marker.
- **Project-level Skills:** canonical Skills carrying the user-selected `project` scope marker, including Skills already linked into one or more projects and Skills preinstalled before any project exists.
- **Unclassified Skills:** legacy or incomplete canonical entries without a marker; show them explicitly and ask for classification before the next scope-dependent operation.

Treat the explicit marker as the source of truth. Use symlinks to validate whether the selected scope is correctly exposed, not to infer the user's classification. Include recorded project destinations as supplemental information when available. Do not omit a project-level Skill merely because the current project cannot discover it.

## Manage groups

Store group manifests at `$HOME/SkillsLibrary/groups/<group-name>.yaml`. A manifest refers to canonical Skill names and never contains copies.

- Create: `group create <group>`
- Inspect: `group show <group>` or `group list`
- Add explicit members: `group add <group> <skill>...`
- Remove members: `group remove <group> <skill>...`
- Delete: `group delete <group>`
- Install/expose all members: `group expose <group> --scope ...`

Always dry-run mutations first. Do not support nested groups in version 1. A Skill may belong to multiple groups.

When the user says "install backend skills", resolve `backend` as a group, show all members, and ask the mandatory scope question once. For a project-level installation, ask whether a root is available now. If yes, preflight the complete group, create one flat symlink per member inside the selected `.agents/skills/` directory, and mark every member `project`. If no, install and validate all canonical members, then mark every member `project` without creating links. Never link the group manifest or group directory itself.

## Migrate existing Skills

Only enter this workflow after explicit consent.

1. Discover user-level candidates:

   ```bash
   python3 scripts/skill_manager.py discover
   ```

2. Include project candidates only when the user supplies project roots with `--project`.
3. Present candidates and exclusions. Do not traverse arbitrary home or filesystem trees.
4. For each real Skill directory selected by the user, ask global or project-level and, when needed, ask for the project root.
5. Dry-run `migrate <source> --scope ...`, then apply after confirmation.
6. Treat existing symlinks separately: if already pointing into the library, register or repair exposure; if pointing elsewhere, inspect the target and ask before adopting it.
7. Mark migration `completed` only after the user-selected set succeeds. A declined first-run prompt does not disable later manual migration.

## Update, unlink, remove, and repair

- **Update:** Obtain a completed replacement directory, dry-run `adopt <source> --replace`, then apply after explicit confirmation. Canonical path stability keeps existing links intact.
- **Unlink:** Use `unexpose`; remove only the selected symlink and never the canonical Skill.
- **Scope change:** The user owns classification. Use `set-scope`; remove a global link before changing a Skill from `global` to `project`, and create the global link through `expose` when changing it to `global`.
- **Preinstalled project Skill:** Its `project` scope marker is sufficient; it needs no separate pending or binding state. Keep the canonical copy until the user supplies a project root or explicitly removes it.
- **Remove:** Use `remove`; refuse while recorded exposures, a live global link, or group memberships remain. Removal moves the canonical directory to a recoverable backup rather than permanently deleting it.
- **Repair:** Run `doctor`. Show broken links, invalid or missing scope markers, scope/link contradictions, missing group members, and stale recorded exposures. Repair only the user-approved items.

## Conflict handling

For any collision, stop and show:

- requested source and canonical destination;
- existing target type and resolved path;
- affected scope or project root;
- safe choices: use existing, explicitly replace with backup, choose another name, or cancel.

Never silently merge Skill folders. Do not delete a symlink target when unlinking. Do not treat a directory junction or an ordinary directory as a removable symlink.

## Script contract

Use `scripts/skill_manager.py` for deterministic filesystem work. Commands are read-only by default where a mutation is possible and require `--apply` to change state. The script uses only the Python standard library, enforces the official frontmatter constraints needed for installation, writes state atomically, stages canonical replacements, retains recoverable backups, and rolls back newly created links, removed links, and moved canonical or group paths when a transaction fails.

After modifying the script, run `python3 scripts/test_skill_manager.py` and the bundled `skill-creator` `quick_validate.py` before accepting the change.

Run `python3 scripts/skill_manager.py --help` for the command index.
