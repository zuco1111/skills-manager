---
name: skills-manager
description: Install and manage standalone Codex Skills—not plugins or bundled/system/plugin-cache Skills—through a central SkillsLibrary with mandatory global-or-project scope selection and default functional-overlap checks that prevent ambiguous Skill routing. Use when users ask to initialize Skills Manager; install, import, migrate, list, classify, group, update, repair, expose, unlink, remove, deduplicate, compare, or inspect functionally similar Skills; preinstall project Skills before a project exists; install named Skill groups such as "backend skills"; or ask about Skills Manager features and documentation. Store canonical copies in SkillsLibrary and expose them with symlinks. Use skill-creator, not this Skill, to author Skill content.
---

# Skills Manager

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
- Keep functional-overlap checks enabled by default unless the user explicitly disables them. Use them to prevent ambiguous Skill routing, not to detect filesystem copies. Never merge or remove Skills automatically.
- Never overwrite a real directory, conflicting symlink, canonical Skill, or group manifest without showing the conflict and receiving explicit confirmation for the selected resolution.
- Exclude bundled system Skills, administrator Skills, and plugin-managed cache directories from migration by default.
- Run a dry run before every mutating script command. Explicit migration consent authorizes conflict-free migrations, and a completed initial overlap review authorizes its bookkeeping marker. For other mutations, run the same command with `--apply` only after the user confirms the exact plan.

## Load the feature guide when needed

Read [references/user-guide.md](references/user-guide.md) completely when the user asks what this Skill supports, requests documentation, asks how groups or migration work, or needs examples. Answer in the user's language.

## Start every workflow

1. Resolve this Skill's actual directory from the active Skill path.
2. Run `python3 scripts/skills_manager.py status`.
3. If `canonical_manager_valid` is `false` or `global_manager_link_status` is not `already-correct`, immediately tell the user that Skills Manager must be initialized before use. Offer to initialize it now and follow **Initialization** before managing other Skills.
4. If `overlap.enabled` is `true` and `overlap.initial_scan_done` is `false`, follow **Functional overlap checks** and complete the one-time canonical scan before the requested operation.
5. If `migration_status` is `not-asked`, ask whether the user wants to scan existing user-level Skills for migration. Record the answer with `mark-migration`; do not scan before consent.
6. Identify the requested operation and follow the relevant workflow below.

## Initialization

Use initialization when this Skill was installed outside the canonical library or `status` reports an invalid canonical manager or an incorrect global manager link.

1. Lead with a clear notice in the user's language: Skills Manager has been installed, but it must be initialized before it can be used.
2. Explain that initialization will place the real copy at `$HOME/SkillsLibrary/skills/skills-manager` and create the global entry as a symlink.
3. Show a dry run, passing the currently active Skill folder explicitly:

   ```bash
   python3 scripts/skills_manager.py initialize --source <active-skill-folder>
   ```

4. After confirmation, rerun with `--apply`.
5. Report the canonical path, symlink path, and any recoverable backup path.
6. Run the one-time canonical functional-overlap scan described below and record it only after semantic review is complete.
7. Tell the user the initialized Skill will be used on the next turn; if it does not appear, restart Codex.

The initialization command must be idempotent. It must validate the copied Skill before switching paths and roll back on failure. Keep any legacy CLI compatibility internal and do not surface its old name or terminology in user-facing messages.

## Functional overlap checks

Treat functional overlap as a routing-conflict check. The script only returns broad lexical candidates; the Agent makes the semantic decision.

1. Run `python3 scripts/skills_manager.py overlap scan` for the one-time canonical scan, or add one repeated `--candidate <skill-directory>` for each staged, local, imported, or migrating candidate.
2. Use `lexical_candidates` as the priority list, then review the `scanned_items` names and descriptions for obvious semantic equivalents that share few literal words. Compare only plausible same-object items; do not perform a full body review of unrelated Skills. If `name` and `description` are insufficient for a plausible pair, read only the bodies of those two `SKILL.md` files; do not inspect other bundled files for this decision.
3. Mark a pair highly overlapping only when all four conditions hold:
   - both Skills operate on the same object;
   - their core actions overlap or one contains the other;
   - common user requests would plausibly trigger both;
   - no clear routing boundary distinguishes them.
4. Warn when one Skill is a capability subset of the other. Do not warn merely because Skills share a domain while performing different actions.
5. If no pair is highly overlapping, continue without adding an overlap confirmation.
6. If any pair is highly overlapping, show all affected pairs together. For each pair, include both descriptions, scope, exposures, group memberships, the overlap, the meaningful differences, and the routing risk. Include the exact dependency cleanup and backup consequence under **keep new**. Ask the user to choose **keep both**, **keep existing**, **keep new**, or **cancel** for each affected candidate. For a canonical-only scan, use the actual Skill names instead of ambiguous existing/new labels.
7. Apply the choice safely:
   - **Keep both:** continue without merging either Skill.
   - **Keep existing:** skip the new candidate and continue unaffected batch items.
   - **Keep new:** first adopt, validate, and expose the new Skill using the user-selected scope. Then execute only the displayed cleanup plan for the existing Skill, removing group memberships before exposures and using `remove` for a recoverable backup. If project scope was selected without an existing root, do not clean up the existing Skill; keep it active and defer retirement until the new Skill can be exposed in a supplied project root. If cleanup fails, keep the new Skill active, report exactly which old dependencies or canonical paths remain, and offer the normal repair workflow. Do not claim an atomic switchover and do not request a second confirmation for the same exact plan.
   - **Cancel:** stop the affected installation or import; do not alter either Skill.

Never infer high overlap from the script's lexical score alone. Keep same-name collisions and replacements in the normal conflict or update workflows.

After completing the post-initialization semantic review, dry-run `overlap mark-initial-scan`, then apply this bookkeeping update without a separate prompt when no high overlap exists. If high overlap exists, include the marker in the consolidated decision plan. To change the default, dry-run `overlap set on|off`, then apply it after explicit user confirmation.

## Install or import one Skill

1. Ask the mandatory scope question: global or project-level.
2. For project-level, ask whether a project or module root exists now. If yes, ask for and confirm it. If no, confirm that only the canonical library copy will be installed and no link will be created yet.
3. Resolve and validate the source:
   - For an OpenAI curated Skill or GitHub source, use the available `skill-installer` workflow to create a temporary staged Skill outside the canonical destination. Do not expose or promote it yet.
   - For a local completed Skill, use the source directory as the candidate.
   - For a newly authored Skill, let `skill-creator` finish and validate its contents first, then treat that directory as a local candidate.
4. Run `overlap scan --candidate <skill-directory>` and complete the semantic review before any `adopt`, canonical promotion, or exposure. If the check is disabled, report that it was skipped and continue.
5. For an approved local or staged candidate, run `adopt <source>` as a dry run, then `adopt <source> --apply` after confirmation.
6. Validate the canonical directory:

   ```bash
   python3 scripts/skills_manager.py validate <canonical-skill-directory>
   ```

7. If a destination exists, show the exposure plan:

   ```bash
   python3 scripts/skills_manager.py expose <skill-name> --scope global
   python3 scripts/skills_manager.py expose <skill-name> --scope project --project <root>
   ```

8. After confirmation, repeat the applicable exposure command with `--apply`; exposure records the selected scope. If the project does not exist yet, skip exposure and record the user's classification instead:

   ```bash
   python3 scripts/skills_manager.py set-scope <skill-name> --scope project
   python3 scripts/skills_manager.py set-scope <skill-name> --scope project --apply
   ```

   The installation is complete once the canonical copy is valid and its scope is marked.
9. A project-level Skill without a project link is intentionally undiscoverable by Codex until the user later supplies a root; then use `expose --scope project --project <root>`.
10. Do not ask about group membership unless the user already requested a group operation.

If installation produces an invalid Skill, leave it unexposed and report the validation errors.

## Install multiple Skills

Ask for scope once for the batch. For project-level, ask whether one root exists now unless the user requests mixed destinations. Stage or identify every candidate, then run one `overlap scan` with repeated `--candidate` arguments and review all returned pairs together before mutation. Continue without an overlap confirmation when no pair is highly overlapping. If no project root is available, install and validate all approved canonical members, then mark the entire batch `project` with one `set-scope <skill>... --scope project` transaction without creating links. When a root is supplied, preflight every approved Skill and link before applying changes. If any member has a path conflict, stop the batch rather than leaving a partial installation.

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
   python3 scripts/skills_manager.py discover
   ```

2. Include project candidates only when the user supplies project roots with `--project`.
3. Present candidates and exclusions. Do not traverse arbitrary home or filesystem trees.
4. For each real Skill directory selected by the user, ask global or project-level and, when needed, ask for the project root.
5. Run one `overlap scan` with every selected real directory as a repeated candidate and complete the consolidated semantic review.
6. Dry-run `migrate <source> --scope ...` for every remaining selected real directory before applying that item.
7. Immediately rerun each conflict-free migration with `--apply`. The user's consent to migrate and selected scope or destination are sufficient authorization; do not ask for a second confirmation of the dry-run plan.
8. Continue applying conflict-free items even when other selected items conflict. Present only the conflicting items for confirmation, with the conflict details and safe resolution choices. Do not include already applied or conflict-free items in that confirmation request.
9. After the user resolves a conflict, apply only the approved resolution without reconfirming the whole migration set.
10. Treat existing symlinks separately: if already pointing into the library, register or repair exposure; if pointing elsewhere, inspect the target and ask before adopting it.
11. Mark migration `completed` only after the final user-selected set succeeds. If the user excludes a conflicting item, remove it from the selected set before marking completion. A declined first-run prompt does not disable later manual migration.

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

During migration, a collision stops only the affected item. Apply all other conflict-free selected items immediately, and ask the user only how to resolve or skip the conflicting item.

## Script contract

Use `scripts/skills_manager.py` for deterministic filesystem work. Commands are read-only by default where a mutation is possible and require `--apply` to change state. The script uses only the Python standard library, enforces the official frontmatter constraints needed for installation, writes state atomically, stages canonical replacements, retains recoverable backups, and rolls back newly created links, removed links, and moved canonical or group paths when a transaction fails.

After modifying the script, run `python3 scripts/test_skills_manager.py` and the bundled `skill-creator` `quick_validate.py` before accepting the change.

Run `python3 scripts/skills_manager.py --help` for the command index.

The standard Agent installation workflow routes sources through staging, but direct invocation of low-level installer scripts, filesystem copies, and other workflows that bypass Skills Manager also bypass its overlap check. Do not claim Hook-level interception. When such Skills are later discovered, run a manual canonical overlap scan before the next managed installation decision.
