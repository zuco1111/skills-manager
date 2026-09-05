---
name: skills-manager
description: Manage standalone agent Skills across Codex, Claude Code, OpenClaw, and Hermes using one canonical SkillsLibrary and host-specific symlinks. Use when users ask to install or uninstall a Skill; make it available to or remove it from an agent; import, migrate, list, group, update, repair, or change its user/project/profile/shared/agent scope; reuse an installed Skill; resolve duplicate or conflicting versions; inspect functional overlap; or initialize or repair Skills Manager. Exclude plugins and bundled, system, administrator, or plugin-cache Skills. Use skill-creator to author content and low-level installers only to fetch or stage sources.
---

# Skills Manager

Keep one canonical copy of every managed Skill and expose it independently to each compatible agent host. Keep only unresolved decisions interactive and filesystem changes deterministic.

## Core model

- Store canonical directories only under `$HOME/SkillsLibrary/skills/<skill-name>` unless the user explicitly configured another library root.
- Treat an installation as a host-aware exposure record plus, when a destination exists, a symlink to the canonical directory. Never create a second canonical copy for another host.
- A Skill may be installed for multiple hosts with different scopes. Changing or removing one host installation must not change another.
- Use `--host codex|claude-code|openclaw|hermes` for every new host-aware operation. Omitting `--host` retains legacy `$HOME/.agents/skills` behavior only for compatibility; never choose that path for a new installation.
- Reserve one exception for Skills Manager itself: always keep `$HOME/.agents/skills/skills-manager` as the shared bootstrap symlink to its canonical directory. Do not migrate, unexpose, or replace that bootstrap with a Codex, OpenClaw, or Hermes user entry. Claude Code gets an additional compatibility entry only when explicitly requested.

Use these host/scope mappings:

| Host | User-wide scope | Project or agent scope |
|---|---|---|
| Codex | `global` -> `${CODEX_HOME:-$HOME/.codex}/skills/` | `project` -> `<root>/.agents/skills/` |
| Claude Code | `global` -> `$HOME/.claude/skills/` | `project` -> `<root>/.claude/skills/` |
| OpenClaw | `global` -> `${OPENCLAW_STATE_DIR:-$HOME/.openclaw}/skills/` | `agent` -> `<agent-workspace>/skills/` |
| Hermes | `global` -> `${HERMES_HOME:-$HOME/.hermes}/skills/` | `project` -> `<root>/.hermes/skills/` |

`--state-dir` may override the OpenClaw shared state root, `--workspace` selects an OpenClaw agent workspace, and `--profile-home` may override the Hermes profile root.

An exposure plan may also report runtime requirements. `openclaw-allow-symlink-target` means OpenClaw must trust the exact canonical target before loading an agent-workspace symlink. `hermes-project-trust` means Hermes must trust the named project before loading its project Skill. The script reports these requirements but does not edit either runtime's configuration.

## Non-negotiable rules

- Determine the target host before choosing scope. In a recognized host, use the current host without asking the ordinary user to select it; if the user explicitly names another host, honor that host. If the runtime host is unknown, ask which supported host to target. If the requested host is incompatible, explain why and stop before mutation.
- Resolve a valid scope for that host before each single-Skill, batch, or group installation. Ask once per host transaction only when the request does not already determine the scope or requests mixed targets.
- For Codex, Claude Code, or Hermes project scope, resolve whether an existing project/module root is available. For OpenClaw agent scope, resolve whether an existing agent workspace is available. Accept the root, not its nested Skills directory. Ask only when the root is missing, ambiguous, or normalization would materially change the intended target.
- When a selected project/module root or OpenClaw agent workspace does not yet exist, adopt and validate the canonical copy, record the selected host and scope without an exposure, and create no placeholder directory or separate pending flag.
- When the user selects a user-wide/global installation, allow the standard host discovery directory to be created. Host executable detection is not a prerequisite; Claude Code detection affects only the optional Skills Manager compatibility offer. Use canonical-only `set-scope` instead only when the user explicitly wants no discovery directory yet.
- Do not infer project, module, workspace, state, or profile roots.
- Do not ask whether to add a newly installed Skill to a group. Add membership only on explicit request.
- Treat low-level installers as source acquisition only. Stage outside the canonical library and host discovery paths; Skills Manager owns final adoption, version choice, host scope, and exposure.
- Never initialize Git automatically or mention Git during normal onboarding.
- Keep functional-overlap checks enabled by default. Use them to prevent ambiguous routing, never to infer filesystem duplication, and never merge or remove Skills automatically.
- Never overwrite a real directory, conflicting symlink, canonical Skill, installation record, or group manifest without displaying the conflict and receiving explicit confirmation for the chosen resolution.
- When the same canonical Skill name already exists, compare its content before adoption. Reuse byte-equivalent content without copying it again. If content differs, use the returned paths as an index and read both `SKILL.md` files completely. Before asking, give compact decision support: two to four bullets covering only material differences in triggers, capabilities, workflow, safety, dependencies, or outputs; one recommendation with a reason; and one sentence naming every affected host/scope. Inspect another changed text file only when needed to understand a material difference. Do not lead with raw paths, fingerprints, or line-by-line diffs unless the user asks. Then ask **use existing**, **use incoming**, or **cancel**. If both versions have valuable unique behavior, recommend **cancel** and offer a separate `skill-creator` merge; never merge or select a version implicitly.
- Exclude bundled system Skills, administrator Skills, and plugin-managed caches from migration by default.
- Validate the required `name` and `description` fields and basic frontmatter structure. Preserve and ignore all other fields, including unknown extensions such as `cli_version`; do not reject, rewrite, interpret, or add field-specific checks for them. Filesystem safety and conflict checks still apply. Successful validation does not establish host compatibility or CLI version requirements.
- Run a dry run before every mutating script command. If it succeeds, matches the resolved request, and reports no conflict or unresolved decision, immediately repeat the command with `--apply`; do not pause for a second confirmation. Ask only when the dry run exposes a conflict or overwrite resolution, version or overlap choice, ambiguous host, scope, root, target, or batch selection, missing authorization or runtime trust, or another issue the user must decide. A choice already made explicitly in the request or earlier in the workflow must not be reconfirmed.
- Never silently reinterpret version-1 `skill_scopes` or legacy `$HOME/.agents/skills` exposures as Codex, Claude Code, OpenClaw, or Hermes installations. Migration requires a dry run and an explicit host/scope mapping; after those choices are resolved, apply a conflict-free plan without another confirmation.
- Inspect every returned `requirements` entry. A created symlink is not yet usable when a runtime requirement remains unmet; report it, identify the exact target or project, and do not claim availability until the host's trust or allowlist requirement is completed.

## Load detailed guidance selectively

Use the table of contents in [references/user-guide.md](references/user-guide.md) and read only the sections required by the task:

- Read sections 1–4 for the canonical model, host paths, discovery, or scope selection.
- Read section 5 for initialization, Claude compatibility, or legacy migration.
- Read sections 6–8 for installation, version reuse, overlap, batches, or groups.
- Read sections 9–10 for updates, uninstallation, scope changes, removal, repair, or safety.
- Read sections 11–12 for examples or documented limitations.

Read the complete guide only when a request spans several areas or reviews the Skill itself. Answer in the user's language.

## Start every workflow

1. Resolve this Skill's actual directory from the active Skill path.
2. Resolve the current or explicitly requested host. Clarify an unknown host before any mutation.
3. Run `python3 scripts/skills_manager.py status` and inspect the canonical manager, host-aware `installations`, legacy state, overlap state, and migration state.
4. If the canonical manager or shared bootstrap is invalid, offer initialization before managing other Skills. A missing Claude Code compatibility entry matters only when Claude Code is the explicit target.
5. If overlap checking is enabled and its initial scan is incomplete, follow **Functional overlap checks**.
6. If migration has not been discussed, ask whether to scan the relevant host's existing user-level Skills and, separately, any legacy Skills Manager entries. Pass one `--host` per approved host, add only user-supplied `--project` or `--workspace` roots, and use `--include-legacy` only after separate consent.
7. Follow the requested workflow.

## Initialization

1. Explain that initialization keeps the real copy at `$HOME/SkillsLibrary/skills/skills-manager` and always creates or verifies this shared bootstrap:

   ```text
   $HOME/.agents/skills/skills-manager
       -> $HOME/SkillsLibrary/skills/skills-manager
   ```

   This reserved bootstrap exception applies only to Skills Manager; all other Skills use the host matrix above.
2. Dry-run with the active directory and an explicit host:

   ```bash
   python3 scripts/skills_manager.py initialize --source <active-skill-folder> --host <host>
   ```

3. If the dry run is conflict-free and matches the resolved request, repeat it immediately with `--apply` without another confirmation.
4. For `--host codex`, `openclaw`, or `hermes`, create no additional manager link or host installation record. Those hosts use the shared bootstrap or their supported external Skill discovery/configuration.
5. For an explicit `--host claude-code`, create `$HOME/.claude/skills/skills-manager` in the same transaction as the bootstrap and record only that compatibility installation.
6. When another host requested initialization and status reports `claude_compatibility.offer: true`, explain that a Claude Code executable was detected and ask whether the user also wants the Claude compatibility entry. Detection is advisory only: never create it until the user agrees, then dry-run `initialize --host claude-code` and apply immediately if the preflight is conflict-free.
7. Report the canonical path, bootstrap, optional Claude compatibility entry, and recoverable backup if one was created. Complete the one-time overlap review; restart the relevant client if discovery is delayed.

Initialization must validate before switching paths and roll back on failure.

## Functional overlap checks

The script supplies broad lexical candidates; the Agent makes the semantic decision.

1. Run `overlap scan`, adding one `--candidate <skill-directory>` per staged, local, imported, or migrating candidate.
2. Prioritize `lexical_candidates`, then inspect names and descriptions for plausible same-object equivalents. Read only the two `SKILL.md` bodies when metadata is insufficient.
3. Mark high overlap only when both Skills operate on the same object, their core actions overlap or one contains the other, common requests could trigger both, and no clear routing boundary separates them.
4. If overlap is high, show both descriptions, every host installation and exposure, group memberships, meaningful differences, routing risk, and the exact cleanup/backup consequence. Ask **keep both**, **keep existing**, **keep new**, or **cancel**.
5. For **keep new**, expose and validate the replacement first, then clean up only the installations and memberships in the approved plan. When the new destination is unavailable, keep the existing Skill active until the replacement can be exposed. Report partial cleanup accurately; never claim an atomic switchover.

Never infer overlap from lexical score alone. Treat same-name collisions as conflicts or updates. After the initial semantic review, dry-run then apply `overlap mark-initial-scan`; no second prompt is needed when the completed review found no high overlap. Change overlap settings only from explicit user intent; after a conflict-free dry run, apply the chosen setting without reconfirming it.

## Install or import Skills

1. Resolve the host and a host-compatible scope, asking only when the request leaves the scope unresolved. For multiple hosts, make a separate target plan for each while reusing the same canonical Skill.
2. Resolve the destination root when linking now, asking only when it is missing or ambiguous; otherwise use the explicitly requested canonical-only host/scope installation.
3. Stage remote Skills outside the canonical destination with the available installer, or identify a completed local Skill. Let `skill-creator` finish authoring first.
4. Dry-run `adopt <source>` to check the canonical inventory and content fingerprint.
   - If it reports `content-identical`, reuse the existing canonical directory and do not copy or replace anything.
   - If it reports `version-choice-required`, follow the compact decision-support rule above before asking **use existing**, **use incoming**, or **cancel**. Using existing continues only with the requested host exposure. Using incoming requires a separate dry-run of `adopt <source> --replace`; warn that every host installation switches together.
   - If no canonical copy exists, continue with the normal adoption plan.
5. Run the overlap scan and semantic review when introducing new canonical content. Apply a conflict-free adoption dry run immediately. For replacement, the user's explicit **use incoming** choice authorizes the matching conflict-free `--replace` dry run; apply it without a second confirmation, then validate the canonical directory. A replacement rollback copy exists only during the transaction and is deleted after canonical fingerprint, state write, and previously correct link checks succeed. Report `cleanup_pending` as an error requiring inspection; do not call runtime availability validated.
6. Dry-run the applicable exposure, always passing `--host`:

   ```bash
   python3 scripts/skills_manager.py expose <skill> --host codex --scope global
   python3 scripts/skills_manager.py expose <skill> --host codex --scope project --project <root>
   python3 scripts/skills_manager.py expose <skill> --host claude-code --scope project --project <root>
   python3 scripts/skills_manager.py expose <skill> --host openclaw --scope agent --workspace <root>
   python3 scripts/skills_manager.py expose <skill> --host hermes --scope global [--profile-home <root>]
   ```

7. Inspect the dry-run `requirements`. For OpenClaw agent scope, surface `openclaw-allow-symlink-target` and the exact canonical `target`. For Hermes project scope, surface `hermes-project-trust` and the exact `project`. These requirements do not require a second confirmation for the filesystem exposure; ask only if satisfying one needs a new user decision or authorization.
8. If the dry run is conflict-free and matches the resolved target, repeat it immediately with `--apply`. When a destination is unavailable, use host-aware `set-scope` to record the host/scope without a link.
9. After linking, complete or have the user complete the reported runtime requirements through the relevant host's supported trust mechanism, then verify availability. Do not imply `skills_manager.py` changed OpenClaw or Hermes configuration. If a requirement remains, report the link as created but runtime access as pending.
10. For a batch, scan all candidates together and preflight every canonical and exposure path before mutation. Present all version conflicts together as one compact block per Skill and accept one reply that maps choices to Skills; do not force sequential prompts. A conflict stops only the affected migration item, but stops a normal batch before partial installation.

A canonical-only installation is intentionally undiscoverable through that target until later exposure. An invalid Skill remains unexposed.

## Discovery across hosts

Host-aware installation records describe what Skills Manager created; they do not disable discovery rules implemented by an agent runtime. In particular, when OpenClaw or Hermes operates in the same project as Codex, it may discover the project's `.agents/skills/` entries after its trust and eligibility checks. This is expected and requires no extra registration or blocking configuration. If it uses another workspace or is not running in that project, it does not discover those project entries.

Do not convert that shared project discovery into a duplicate OpenClaw or Hermes installation record. User-wide host directories remain isolated.

## List, groups, and lifecycle operations

- **List:** Use `status` to report the complete canonical inventory and `installation_status` for every host/scope link or canonical-only target. Use `doctor` when the user also asks for health diagnostics. Report legacy `skill_scopes` separately as unclassified legacy state until migrated.
- **Groups:** Store manifests at `$HOME/SkillsLibrary/groups/<group>.yaml`. They contain canonical names, never copies or nested groups. Resolve the host and scope once, then use host-aware `group expose` to create one flat symlink per member. Inspect and satisfy each returned runtime requirement before calling the group usable.
- **Update:** Start with `adopt <source>`. Reuse identical content. For differing content, provide the compact semantic comparison, recommendation, and impact before obtaining the user's version choice; only **use incoming** proceeds to a dry-run of `adopt <source> --replace`, which is applied immediately when it matches that choice and is conflict-free. Stable canonical paths preserve every correct host link, and the transaction-only rollback copy is deleted after local validation.
- **Unexpose:** Remove only the selected host/scope symlink and installation exposure record. Never follow or remove the canonical target or another host's entry. The reserved Skills Manager bootstrap cannot be unexposed; its optional Claude compatibility entry can be.
- **Unset canonical-only scope:** Use `unset-scope <skill>... --host <host> --scope <scope>` only for matching installations whose `link` is `null`. Dry-run first, then apply immediately when it is conflict-free and matches the request. If an installation has a link, use `unexpose` instead.
- **Scope change:** Preflight removal of the old host exposure or canonical-only scope and creation or classification of the new target as one explicit plan. Do not change installations for other hosts.
- **Remove:** Refuse canonical removal while any installation remains, including a canonical-only `link: null` installation, or while any legacy exposure or group membership remains. Move the canonical directory to a recoverable backup only after all dependencies are cleared.
- **Repair:** Run `doctor`; report invalid canonical Skills, broken or redirected recorded/managed host links, stale installation records, missing group members, incompatible host/scope pairs, and legacy state requiring migration. Use `discover` when the user wants to inspect unrecorded host directories. For a missing, redirected, or unrecorded symlink whose host/scope is known, dry-run `repair <skill> --host ... --scope ...` and apply immediately when it is conflict-free and matches the request. Treat repointing a redirected or conflicting symlink as a decision unless the user already explicitly authorized that resolution. It may create, repoint, or register only that symlink and refuses real directories. Handle canonical content, group manifests, and ambiguous state through their dedicated workflows rather than forcing a link repair.

## Migration

Enter migration only after explicit consent. Run `discover` with one `--host` per approved host; add `--include-legacy` only after separate consent and add only user-supplied `--project` or OpenClaw `--workspace` roots. Omitting `--host` is legacy all-host compatibility and must not be used for a new scoped scan. Present candidates and exclusions. For each selected real directory, require a host and compatible scope, consolidate overlap review, dry-run the exact mapping, and apply it immediately when conflict-free. Ask only about unresolved migration items.

When a migration source has the same name as an existing canonical Skill, resolve it through the compact version decision-support rule before continuing. A bare statement such as “`SKILL.md` differs” is not sufficient decision support.

Treat symlinks separately: register or repair links already targeting the library; inspect external targets before adoption. Continue conflict-free items when another migration item conflicts. Mark migration complete only after the final selected set succeeds.

When explicit `migrate <legacy-real-directory> --host ...` succeeds, it removes a legacy exposure in the same transaction only when the source path exactly matches that exposure record. Clear the same-name legacy scope only when no other legacy exposure remains. A same-name `skill_scopes` marker without an exact source/exposure match is never removed; the dry run reports only `legacy_scope_present`. On failure, do not report either migration or legacy cleanup as complete. Never pass Skills Manager itself to `migrate`; use `initialize`, which preserves its reserved bootstrap and adds only an explicitly requested Claude compatibility entry.

For version-1 state, show every legacy classification and exposure, ask where it belongs, and keep it unchanged until the user selects an explicit mapping. Dry-run that mapping and apply it immediately when conflict-free. Never assume legacy `global` means Codex global or legacy `project` belongs to the current project.

## Script contract

Use `scripts/skills_manager.py` for deterministic filesystem work. Mutations are dry-run by default and require `--apply`. The script uses only the Python standard library, validates required metadata and basic frontmatter structure, preserves extension fields without interpreting them, writes state atomically, retains recoverable backups for migration/removal operations, and rolls back transaction changes on failure. Version replacement uses a transaction-only rollback copy that is deleted after successful local validation.

After modifying the script, run `python3 scripts/test_skills_manager.py` and the bundled `skill-creator` `quick_validate.py`. Use `python3 scripts/skills_manager.py --help` for the exact command index.

Low-level installers and filesystem copies can bypass Skills Manager and its overlap check. Do not claim Hook-level interception; scan externally placed Skills when they are later brought under management.
