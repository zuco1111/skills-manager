# Skills Manager feature guide

## Contents

1. Purpose and boundaries
2. Canonical library and installation model
3. Host paths and discovery behavior
4. Choosing a host and scope
5. Initialization and legacy migration
6. Installing and importing Skills
7. Functional-overlap checks
8. Groups
9. Updates, unexposure, removal, and repair
10. Safety behavior
11. Examples
12. Current limitations

## 1. Purpose and boundaries

Skills Manager manages standalone agent Skills for Codex, Claude Code, OpenClaw, and Hermes. It keeps exactly one canonical copy of each managed Skill and creates host-specific directory symlinks so each agent sees only the installations relevant to it.

It is responsible for:

- initializing the canonical library and Skills Manager itself;
- importing completed Skills into that library;
- selecting a host and a valid user, project, profile, shared, or agent-workspace scope;
- exposing the same canonical Skill independently to multiple hosts;
- preinstalling a scoped Skill before its project or workspace exists;
- migrating user-selected existing and legacy Skills after a conflict-free dry run;
- detecting functional overlap that could cause ambiguous routing;
- defining reusable groups of canonical Skill names;
- validating, updating, unexposing, removing, and repairing managed Skills.

It does not manage plugins, bundled system Skills, administrator-managed Skills, or plugin caches. It also does not author Skill instructions: use `skill-creator` to finish a Skill, then use Skills Manager for canonical placement and exposure.

Skills Manager is policy plus a deterministic local script, not an installation Hook. Direct filesystem copies or other installers can bypass its overlap and state checks.

## 2. Canonical library and installation model

The default library is:

```text
$HOME/SkillsLibrary/
├── skills/
│   ├── skills-manager/
│   ├── fastapi/
│   └── redis/
├── groups/
│   └── backend.yaml
└── .skills-manager/
    ├── state.json
    └── backups/
```

Only `SkillsLibrary/skills/<skill-name>` contains the managed canonical directory. Host discovery directories contain symlinks, never independent managed copies:

```text
host discovery path/<skill-name>
    -> $HOME/SkillsLibrary/skills/<skill-name>
```

An installation consists of:

- a host (`codex`, `claude-code`, `openclaw`, or `hermes`);
- a host-compatible scope;
- an optional destination root;
- the expected exposure path and its status.

The same Skill may have several independent installations. For example, `database-tools` can be user-wide in Codex, project-level in Claude Code, and agent-specific in OpenClaw while all three entries point to one canonical directory. Removing the Claude Code link does not change the Codex or OpenClaw installations.

The host-aware `installations` state is the classification source for new installations. Symlinks verify discoverability; they do not replace the recorded intent. A scoped installation without a destination link is a valid preinstallation and needs no separate pending field.

Version-1 `skill_scopes` and legacy `$HOME/.agents/skills` entries remain separately identifiable until explicitly migrated. They are not silently converted into a host-aware installation.

Skills Manager itself is the reserved bootstrap exception. Its canonical directory still lives in the library, but it always keeps this shared discovery link:

```text
$HOME/.agents/skills/skills-manager
    -> $HOME/SkillsLibrary/skills/skills-manager
```

That link is not a Codex user-wide installation and is not a migration candidate. It exists so an already-installed Skills Manager can manage host-specific installations. No other newly installed Skill uses `$HOME/.agents/skills` as a user-wide target.

Skills Manager does not initialize or modify Git during normal workflows.

## 3. Host paths and discovery behavior

### Supported target matrix

| Host | User-facing choice | CLI scope | Exposure path |
|---|---|---|---|
| Codex | User-wide | `global` | `${CODEX_HOME:-$HOME/.codex}/skills/<skill-name>` |
| Codex | Project/module | `project` | `<root>/.agents/skills/<skill-name>` |
| Claude Code | User-wide | `global` | `$HOME/.claude/skills/<skill-name>` |
| Claude Code | Project/module | `project` | `<root>/.claude/skills/<skill-name>` |
| OpenClaw | Shared | `global` | `${OPENCLAW_STATE_DIR:-$HOME/.openclaw}/skills/<skill-name>` |
| OpenClaw | One agent | `agent` | `<agent-workspace>/skills/<skill-name>` |
| Hermes | Profile-wide | `global` | `${HERMES_HOME:-$HOME/.hermes}/skills/<skill-name>` |
| Hermes | Project | `project` | `<root>/.hermes/skills/<skill-name>` |

`--state-dir <root>` overrides the OpenClaw shared state root for an operation. `--workspace <root>` selects an OpenClaw agent workspace. `--profile-home <root>` overrides the Hermes profile root. Codex respects `CODEX_HOME`; OpenClaw and Hermes respect `OPENCLAW_STATE_DIR` and `HERMES_HOME` respectively.

These combinations are intentionally rejected:

- OpenClaw with `project` scope: use `agent` for an explicit OpenClaw installation, or rely on supported project discovery described below.
- Codex, Claude Code, or Hermes with `agent` scope.
- Project scope without `--project` when creating a link now.
- OpenClaw agent scope without `--workspace` when creating a link now.
- Host-specific override flags on an unrelated host.

### Runtime requirements after linking

Some host/scope combinations impose a runtime trust check in addition to filesystem exposure. The exposure dry run and apply result report these under `requirements`:

- `openclaw-allow-symlink-target`: an OpenClaw agent-workspace link points outside the workspace to the canonical library. OpenClaw must allow or trust the exact canonical `target` reported by Skills Manager before it loads that symlink.
- `hermes-project-trust`: Hermes must trust the exact `project` reported by Skills Manager before it loads Skills from that project's `.hermes/skills/` directory.

`skills_manager.py` creates and records symlinks; it does not automatically change OpenClaw allowlists, Hermes project trust, or other host configuration. Include every requirement in the displayed plan. After linking, complete the requirement through the host's supported trust mechanism and verify discovery before reporting the Skill as available. If the requirement is not completed, report two separate facts: the symlink was created, but runtime access remains pending.

### User-wide isolation

User-wide entries are host-specific. Installing a Skill globally for Codex does not expose it globally to Claude Code, OpenClaw, or Hermes. Expose it separately when the user wants another host to see it.

There is no new user-wide installation target at `$HOME/.agents/skills` for ordinary Skills. The exact `$HOME/.agents/skills/skills-manager` bootstrap is the sole reserved exception. Omitting `--host` on ordinary exposure commands preserves legacy behavior only so existing installations can be inspected, repaired, unexposed, or migrated safely.

### Shared project discovery

A Codex project installation is placed in `.agents/skills`. When OpenClaw or Hermes operates in that same project, its runtime can also discover that entry after applicable trust, eligibility, and runtime checks. It can then invoke the Skill without a second Skills Manager registration.

This does not create an OpenClaw or Hermes installation record: the explicit installation still belongs to Codex, while the other runtime is discovering a shared project entry. Skills Manager should neither duplicate the record nor create a blocking configuration.

Any runtime trust or eligibility needed for this cross-host discovery is still enforced by OpenClaw or Hermes. Skills Manager does not modify that runtime configuration automatically.

If OpenClaw uses another workspace, or Hermes is not running in that project, it does not discover that project entry. Host-specific user directories remain isolated in either case.

## 4. Choosing a host and scope

### Normal flow in a recognized host

When Skills Manager is running inside a recognized agent host, it uses that current host and asks only for choices the request has not already resolved:

- Codex: user-wide or project-level?
- Claude Code: user-wide or project-level?
- OpenClaw: shared or one agent workspace?
- Hermes: profile-wide or project-level?

The user does not need to answer a four-host questionnaire for an ordinary installation. The script command still passes the resolved `--host` explicitly so a new operation cannot fall back to legacy behavior.

If the user explicitly asks to install for another supported host, that target overrides the current host. If the user asks for multiple hosts, create one target plan per host and reuse the same canonical directory.

### Unknown or incompatible host

If the current runtime cannot be identified, ask which of the four supported hosts should receive the installation before choosing scope or constructing a path. Do not guess from an existing directory.

If the named host is unsupported or the requested scope is incompatible, explain the supported choices and stop before mutation. Do not fall back to `$HOME/.agents/skills` and do not silently treat an unknown host as Codex.

### Destination available now

For project, module, or agent scope, determine whether the destination exists now. When it does, use the root itself—not the nested Skills directory. The root must exist, but Skills Manager may create the nested discovery directories. Ask only when the root is missing, ambiguous, or normalization would materially change the intended target.

A user-selected user-wide/global installation may create the standard host discovery directory even when it is currently absent. Detecting a host executable is not a prerequisite for an ordinary Skill installation. Claude Code detection controls only whether initialization offers the optional Claude compatibility entry for Skills Manager itself. If the user wants to preinstall without creating a global discovery directory, record a canonical-only installation with `set-scope` instead.

### Preinstall before a destination exists

If the project, module, or OpenClaw workspace does not exist yet, install and validate the canonical Skill, record the selected host/scope, and create no symlink:

```text
$HOME/SkillsLibrary/skills/<skill-name>   # canonical copy exists
destination discovery link               # not created yet
```

The Skill remains in the complete inventory but is intentionally unavailable through that target until the user later supplies the destination root.

To discard that canonical-only target classification without removing the canonical Skill, dry-run and, when the result is conflict-free and matches the request, immediately repeat with `--apply`:

```bash
python3 scripts/skills_manager.py unset-scope <skill-name> \
  --host <host> --scope <scope>
```

`unset-scope` removes only a matching installation whose `link` is `null`. It refuses linked installations; use `unexpose` with the exact host, scope, and destination information first when a symlink exists.

## 5. Initialization and legacy migration

### Shared bootstrap and optional Claude compatibility

Initialization places or validates the canonical Skills Manager at:

```text
$HOME/SkillsLibrary/skills/skills-manager
```

and always creates or verifies:

```text
$HOME/.agents/skills/skills-manager
    -> $HOME/SkillsLibrary/skills/skills-manager
```

Pass the requesting host in the dry run so the policy is explicit:

```bash
python3 scripts/skills_manager.py initialize \
  --source <active-skill-folder> \
  --host codex
```

When the dry run is conflict-free and matches the resolved request, immediately repeat it with `--apply` without asking for another confirmation. Initialization validates a staged copy before switching paths, preserves recoverable backups, preflights every requested entry, writes state once, and rolls back the canonical directory, backup move, and newly created links together on failure.

- `--host codex`, `--host openclaw`, and `--host hermes` create no additional host-native manager link or installation record. They keep only the shared bootstrap.
- An explicit `--host claude-code` also creates `$HOME/.claude/skills/skills-manager` and records that Claude Code compatibility installation.
- Initializing for another host never creates the Claude entry automatically. If Claude Code is detected and the compatibility link is absent, the result reports `claude_compatibility.offer: true`. Ask whether the user wants the entry; only after agreement should you dry-run `initialize --host claude-code`, then apply it immediately when the preflight is conflict-free.

Detection is advisory and currently means an executable `claude` is available on `PATH` or at a standard macOS/Linux launcher path, including `$HOME/.local/bin/claude` and common Homebrew/system binary directories. A `$HOME/.claude` directory alone is not enough because other Claude clients and extensions can also create it. Detection does not prove that Claude Code is currently running, and it never authorizes a filesystem change.

The shared bootstrap is not a legacy manager link to clean up. `migrate`, generic `expose`, legacy `unexpose`, and group exposure reject Skills Manager itself; use `initialize`. The optional Claude compatibility entry may be removed with an explicit Claude Code `unexpose` operation without changing the bootstrap.

After initialization, complete the one-time semantic overlap review. The requested runtime usually discovers the entry on the next turn when it supports the shared bootstrap or configured external discovery; restart its client if necessary. For Claude Code, verify the explicit compatibility link when that host was selected.

### Migration consent and discovery boundaries

The first migration question authorizes only a scan. It does not authorize filesystem changes.

Discovery is limited to known roots for the explicitly approved hosts, legacy `$HOME/.agents/skills` only after separate consent, and project/workspace roots explicitly supplied by the user. Run `discover --host <host>` and repeat `--host` only for each approved host. Add `--include-legacy` only when the user approved the legacy scan. Omitting `--host` preserves the older all-host discovery behavior for compatibility and must not be used for a new scoped scan.

Relevant roots include:

```text
${CODEX_HOME:-$HOME/.codex}/skills
$HOME/.claude/skills
${OPENCLAW_STATE_DIR:-$HOME/.openclaw}/skills
${HERMES_HOME:-$HOME/.hermes}/skills
$HOME/.agents/skills                         # ordinary legacy compatibility sources
<supplied-project>/.agents/skills
<supplied-project>/.claude/skills
<supplied-project>/.hermes/skills
<supplied-openclaw-workspace>/skills
```

Do not scan arbitrary home or filesystem trees. Exclude `.system`, plugin caches, bundled Skills, administrator-managed entries, and the exact reserved `$HOME/.agents/skills/skills-manager` bootstrap by default.

### Migrating version-1 state

Legacy `skill_scopes` values lack a host dimension. Therefore neither of these conversions is safe to infer:

```text
legacy global  -> Codex global
legacy project -> current Codex project
```

For each selected legacy Skill or exposure:

1. Show the existing canonical path, legacy scope, links, and conflicts.
2. Resolve the supported host and compatible scope, asking only when either remains unspecified or ambiguous.
3. Resolve the required project or workspace root when applicable, asking only when it is missing, ambiguous, or materially changed by normalization.
4. Run the consolidated overlap review.
5. Dry-run the exact migration and inspect paths, state changes, backups, and conflicts.
6. Apply immediately when the result matches the selected mapping and is conflict-free; ask only about unresolved items.
7. Leave unselected or unresolved legacy records unchanged and labeled as legacy.

Conflict-free items may continue immediately even if another selected migration item conflicts. Ask only about unresolved items; do not reapply or reconfirm completed ones. Mark migration complete only after the final user-selected set succeeds.

Existing symlinks that already point to the canonical library can be registered or repaired. For links pointing elsewhere, inspect the target and ask before adoption. Never follow a link and delete its target.

An explicit successful `migrate <legacy-real-directory> --host ...` transaction removes a legacy exposure only when the source path exactly matches that exposure record. It clears the same-name legacy scope only when no other legacy exposure remains. If only a same-name `skill_scopes` marker exists and the source does not match a legacy exposure, migration does not remove it; the dry run reports `legacy_scope_present` for later handling. A failed transaction leaves the selected legacy state intact. Skills Manager itself is excluded from this migration flow: `initialize` preserves its reserved bootstrap and adds only an explicitly requested Claude compatibility entry.

## 6. Installing and importing Skills

Supported sources include OpenAI curated Skill names, GitHub repository paths or Skill URLs through an available installer, local completed Skill directories, and completed Skills produced by `skill-creator`. A low-level installer only fetches or stages source content outside the canonical library and host discovery paths. Skills Manager remains responsible for the canonical destination, version decision, host scope, and final exposure.

Metadata validation reads and checks the required top-level `name` and `description`. All other fields, whether standard optional fields or vendor extensions such as `cli_version`, are preserved in the original file and ignored by the manager. They do not need an allowlist, field-specific validation, or a compatibility warning merely because they are unfamiliar. Adoption and migration preserve the file contents, including these fields; existing fingerprint and version-conflict checks still detect changes to them. The manager does not execute extension values, change permissions from them, or establish that the installed CLI satisfies a declared version requirement.

Basic checks still require UTF-8, frontmatter delimiters, supported top-level key syntax, valid required fields, and the existing directory/path safeguards. This standard-library reader is not a complete YAML validator: it skips extension values and their continuations without checking their syntax or meaning. Host parsing and runtime compatibility remain separate from management validation.

The normal sequence is:

1. Resolve the current or requested host.
2. Resolve a compatible scope and, when linking now, the exact destination root; ask only when either is missing or ambiguous.
3. Stage remote content outside the canonical destination or identify the completed local source.
4. Validate the Skill name, directory name, and `SKILL.md` metadata.
5. Dry-run `adopt <source>` to compare the source with the canonical inventory. Reuse `content-identical` without a canonical mutation. For `version-choice-required`, provide the compact semantic comparison, recommendation, and affected host/scopes described below before asking **use existing**, **use incoming**, or **cancel**. A new canonical name proceeds as a proposed adoption.
6. When new canonical content would be introduced—either a new name or an approved incoming version—scan the candidate for functional overlap and complete semantic review.
7. Apply a conflict-free adoption dry run immediately, then validate the canonical copy. For replacement, the user's explicit **use incoming** choice authorizes the matching conflict-free `--replace` plan without another confirmation. Skip this canonical mutation when reusing existing content.
8. Dry-run the host-specific exposure and inspect every returned runtime requirement, or record a canonical-only host/scope installation when no destination exists.
9. Apply the exposure immediately when its dry run is conflict-free and matches the resolved target. Report its runtime requirements; ask only if satisfying one requires a new user decision or authorization.
10. Satisfy and verify any OpenClaw target allowlist or Hermes project trust requirement before reporting the Skill as available.

This sequence applies to ordinary Skills. Installing Skills Manager itself uses the initialization exception in section 5 rather than generic `adopt`, `expose`, or `migrate` commands.

Representative exposure commands are:

```bash
# Codex user-wide
python3 scripts/skills_manager.py expose example \
  --host codex --scope global

# Codex project
python3 scripts/skills_manager.py expose example \
  --host codex --scope project --project /work/payments

# Claude Code project
python3 scripts/skills_manager.py expose example \
  --host claude-code --scope project --project /work/payments

# OpenClaw shared directory
python3 scripts/skills_manager.py expose example \
  --host openclaw --scope global

# One OpenClaw agent workspace
python3 scripts/skills_manager.py expose example \
  --host openclaw --scope agent --workspace /work/agents/researcher

# Hermes profile
python3 scripts/skills_manager.py expose example \
  --host hermes --scope global

# Hermes project
python3 scripts/skills_manager.py expose example \
  --host hermes --scope project --project /work/payments
```

Use `--state-dir` or `--profile-home` only when the user selected a non-default OpenClaw or Hermes root. Mutating commands remain dry-runs until `--apply` is added; the agent adds it immediately after a conflict-free preflight that matches the resolved request.

For OpenClaw agent exposure, the output includes `openclaw-allow-symlink-target` with the canonical target that OpenClaw must trust. For Hermes project exposure, it includes `hermes-project-trust` with the project root Hermes must trust. These are post-link runtime prerequisites, not configuration changes performed by the script.

If a canonical name already exists, compare the incoming directory with the canonical content before deciding anything. The deterministic fingerprint includes relative paths, empty directories, regular-file bytes, symlink targets, and executable bits. It ignores entries named `.git` or `__pycache__`, files named `.DS_Store`, and files ending in `.pyc`.

- If the fingerprints match, report `content-identical`, reuse the one canonical directory, and create only the requested host installation when it is missing. Do not adopt a second copy and do not create a backup.
- If the fingerprints differ, use the added, removed, and changed paths as an inspection index. Read both `SKILL.md` files completely. Inspect another changed text file only when it is needed to understand behavior.
- Present two to four short bullets covering only material differences in triggers, capabilities, workflow, safety, dependencies, or outputs. State plainly when the difference is only formatting or other non-behavioral text.
- Follow with one recommendation and its reason, then one sentence listing every affected host/scope. Do not lead with fingerprints, raw path lists, or line-by-line diffs unless the user asks for details.
- Ask **use existing**, **use incoming**, or **cancel**. Recommend **use existing** for non-behavioral-only differences to avoid churn. Recommend **cancel** and offer a separate `skill-creator` merge when both versions contain valuable unique behavior. Never infer a choice merely from the requesting host.
- **Use existing** leaves the Library unchanged and continues only with the requested host exposure when needed.
- **Use incoming** first dry-runs `adopt <source> --replace`. Explain that all host installations reference the same canonical path and therefore switch versions together. That explicit version choice authorizes the matching conflict-free replacement, so apply it without a second confirmation.
- **Cancel** changes neither canonical content nor host installations.

If a destination exposure path already exists, do not overwrite or merge it. Show the destination type and resolved path, affected host/scope, and safe conflict choices before any mutation.

For a batch, resolve scope once per host target, asking only when the request leaves it ambiguous. Scan every candidate together and preflight all canonical and link paths before mutation. Present all version conflicts together as one compact block per Skill and accept one reply that maps choices to Skills instead of prompting sequentially. A normal batch with a conflicting member stops before leaving a partial installation.

## 7. Functional-overlap checks

Functional overlap is enabled by default. It detects likely agent-routing conflicts, not byte-for-byte duplicates.

Run the one-time canonical scan with:

```bash
python3 scripts/skills_manager.py overlap scan
```

Scan staged or local candidates with repeated paths:

```bash
python3 scripts/skills_manager.py overlap scan \
  --candidate <first-skill-directory> \
  --candidate <second-skill-directory>
```

The script output is a broad lexical filter. Prioritize its candidates, then compare only plausible same-object Skills. Read the two `SKILL.md` bodies only when their names and descriptions do not establish a clear boundary.

A pair is highly overlapping only when all four conditions hold:

- both operate on the same object;
- their core actions overlap or one contains the other;
- common requests could plausibly trigger both;
- no clear routing boundary distinguishes them.

If a pair is highly overlapping, present both descriptions, all host installations and exposures, group memberships, overlap, meaningful differences, and routing risk. Offer:

- **Keep both:** keep both canonical Skills and their independent routes.
- **Keep existing:** skip the new candidate and continue unaffected batch items.
- **Keep new:** expose and validate the new candidate first, then remove only the old installations and memberships in the approved cleanup plan and move the old canonical copy to backup. If the new target has no destination yet, retain the old active Skill until replacement exposure is possible.
- **Cancel:** stop the affected installation without changing either Skill.

Never treat a lexical score as the semantic decision. Same-name collisions use the normal conflict or update workflow.

After the initial semantic review, dry-run and then apply `overlap mark-initial-scan`. A completed no-overlap review authorizes that bookkeeping update without another decision prompt. Enabling or disabling future checks is a separate policy choice and requires explicit user intent.

## 8. Groups

Groups are YAML manifests under `$HOME/SkillsLibrary/groups/` that list canonical Skill names:

```yaml
name: backend
skills:
  - fastapi
  - postgres
  - redis
```

They never contain copies, destination paths, or nested groups. A Skill may belong to several groups.

Use the complete group lifecycle as follows; every mutation is a dry run until a conflict-free result that matches the resolved request is immediately repeated with `--apply`:

- `group list` and `group show <group>` inspect manifests.
- `group create <group>` creates an empty manifest.
- `group add <group> <skill>...` adds explicit canonical members; it does not install them.
- `group remove <group> <skill>...` removes only memberships, not canonical Skills or host links.
- `group rename <group> <new-name>` changes the manifest name and path.
- `group delete <group>` moves only the manifest to a recoverable backup.
- `group expose <group> --host ... --scope ...` creates flat host links after all members pass preflight.

When installing a group:

1. Show all members.
2. Resolve the current or named host.
3. Resolve one compatible scope and destination for the transaction, asking only when the request leaves either ambiguous.
4. Preflight every member and flat link.
5. Include every returned runtime requirement in the group plan.
6. Apply host-aware `group expose` immediately after a conflict-free preflight, then satisfy and verify the requirements before calling the group available.

For example, a Codex project group creates:

```text
<project>/.agents/skills/fastapi
<project>/.agents/skills/postgres
<project>/.agents/skills/redis
```

The same group exposed to Claude Code uses `.claude/skills`; OpenClaw agent scope uses the selected workspace's `skills/`; Hermes project scope uses `.hermes/skills`. The group manifest itself is never linked.

If the project or workspace does not exist yet, validate every canonical member and record canonical-only host/scope installations. Do not create a destination or separate pending marker.

## 9. Updates, unexposure, removal, and repair

### Update

Begin every update with `adopt <source>` even if the user does not remember installing the Skill through another host. Identical content is reused without copying. Differing content produces the compact semantic comparison, recommendation, impact sentence, and version choices without mutation.

When the user explicitly chooses the incoming version, dry-run `adopt <source> --replace` and apply it immediately when the result matches that choice and is conflict-free. The script copies the incoming Skill to staging, verifies that its fingerprint still matches the approved input, moves the old canonical directory to a transaction-only rollback path, promotes and validates the new directory, writes state, and rechecks every host link that was correct before replacement. After those local checks succeed, it deletes the rollback directory immediately. Stable canonical paths keep every correct host link intact.

Local replacement validation proves canonical Skill structure, exact approved content fingerprint, state persistence, and preservation of previously correct symlinks. It does not prove that OpenClaw trust, Hermes project trust, client reload, or runtime discovery succeeded. Verify those separately before claiming the Skill is callable.

If replacement fails before commit, restore the old canonical directory. If only rollback cleanup fails after a valid commit, keep the new canonical version, return `cleanup_pending` with the exact path and error, and make `doctor` unhealthy until the residual transaction directory is inspected and removed. Do not silently retain or register that rollback copy as a long-term backup.

### Unexposure and scope changes

Unexposure removes only the selected host/scope link and its exposure record. It never removes the canonical target or another host's installation. The reserved Skills Manager bootstrap cannot be unexposed; only its optional Claude Code compatibility link can be removed this way.

`unset-scope <skill>... --host <host> --scope <scope>` removes only canonical-only installations with `link: null`. It is dry-run by default; repeat it immediately with `--apply` when the result is conflict-free and matches the request. If the installation has a link, `unset-scope` refuses it and the link must be handled with `unexpose`.

A scope change is an explicit plan within one host: preflight `unexpose` for a linked target or `unset-scope` for a canonical-only target, then create the new exposure or record a new canonical-only target. Do not rewrite other host installations.

### Remove

Canonical removal is refused while any installation remains, including canonical-only `link: null` records, or while any legacy exposure, live managed link, or group membership remains. After every dependency is explicitly cleared, `remove` moves the canonical directory into `.skills-manager/backups/` rather than permanently deleting it.

### Repair

`doctor` checks:

- canonical Skill validity;
- broken, redirected, missing, or conflicting recorded/managed host links;
- stale or incompatible host/scope installation records;
- canonical-only installation records and their recorded classification;
- missing group members;
- legacy state still needing explicit migration.

For a missing, redirected, or correct-but-unrecorded symlink with a known host and scope, dry-run:

```bash
python3 scripts/skills_manager.py repair <skill-name> \
  --host <host> --scope <scope> [destination options]
```

For a missing or correct-but-unrecorded symlink, repeat immediately with `--apply` when the dry run is conflict-free and matches the request. Treat a redirected or conflicting symlink as an unresolved decision unless the user already explicitly authorized repointing it. `repair` may create, repoint, or register only that symlink. It refuses every real directory and never changes canonical content. A failed state write restores the previous symlink. Inspect returned runtime requirements before claiming availability.

Do not force unrelated doctor issues through link repair. Use initialization for the reserved Skills Manager bootstrap, group commands for manifests, version adoption for canonical content, and explicit migration for legacy classifications. Never use another host's correct installation as evidence that a broken target should be silently rewritten.

## 10. Safety behavior

- All mutations are dry-run unless `--apply` is present. Always preflight first.
- When a dry run succeeds, matches the resolved request, and reports no conflict or unresolved decision, immediately repeat it with `--apply` without displaying a confirmation prompt.
- Ask only when preflight exposes a conflict or overwrite resolution, version or overlap choice, ambiguous host, scope, root, target, or batch selection, missing authorization or runtime trust, or another issue the user must decide. Once the user resolves it, rerun any affected preflight and apply a matching conflict-free result without asking again.
- Consent to scan for migration is not consent to mutate. Obtain the user's selected items and explicit host/scope mappings, then preflight and automatically apply each conflict-free matching plan.
- The script reports OpenClaw and Hermes runtime trust requirements but does not edit their configuration; link creation alone is not proof of runtime availability.
- Canonical candidates are staged and validated before promotion.
- State and group files are written atomically.
- Conflicting paths stop the affected operation.
- Claude Code detection only produces an offer; it never creates the compatibility link without explicit user intent for a Claude-targeted initialization.
- Failed batch linking rolls back links created in that transaction.
- Failed state writes restore paths moved or removed by that transaction.
- Ordinary directories and junction-like paths are never treated as removable symlinks.
- Migration and canonical removal retain recoverable backups. Version replacement uses a transaction-only rollback copy and deletes it immediately after successful local validation.
- Permanent deletion, recursive filesystem discovery, automatic Git initialization, automatic updates, and implicit legacy reclassification are outside the workflow.

## 11. Examples

### Initialize Skills Manager for Codex, OpenClaw, or Hermes

```text
$HOME/.agents/skills/skills-manager
    -> $HOME/SkillsLibrary/skills/skills-manager
```

The requesting host does not receive another manager link. If Claude Code is detected, Skills Manager asks whether to add its separate compatibility entry and leaves the answer unmodified until the user chooses.

### Initialize Skills Manager explicitly for Claude Code

```text
$HOME/.agents/skills/skills-manager
    -> $HOME/SkillsLibrary/skills/skills-manager
$HOME/.claude/skills/skills-manager
    -> $HOME/SkillsLibrary/skills/skills-manager
```

Both links are preflighted and applied in one initialization transaction. This explicit Claude entry does not expose any other ordinary Skill to Claude Code.

### Install for the current Codex user

```text
User: Install the example Skill.
Skills Manager: Should this be available user-wide in Codex or only in a project/module?
User: User-wide.
```

The canonical copy is stored once and linked from `${CODEX_HOME:-$HOME/.codex}/skills/example`.

### Install into a Claude Code project

```text
User: Install database-tools for Claude Code in /work/payments.
Skills Manager: I will use Claude Code project scope and link it under /work/payments/.claude/skills.
```

### Install for one OpenClaw agent

```text
User: Install research-tools for my OpenClaw research agent.
Skills Manager: What is the existing agent workspace root?
User: /work/agents/researcher
```

The link is `/work/agents/researcher/skills/research-tools`.

The exposure result also reports `openclaw-allow-symlink-target` for `$HOME/SkillsLibrary/skills/research-tools`. OpenClaw must trust that exact target before the agent can load the linked Skill; Skills Manager does not update the allowlist automatically.

### Install profile-wide in Hermes

```text
User: Make document-tools available in my Hermes profile.
```

The default link is `${HERMES_HOME:-$HOME/.hermes}/skills/document-tools`.

### Install into a Hermes project

```text
User: Install document-tools for Hermes in /work/payments.
Skills Manager: I will create /work/payments/.hermes/skills/document-tools. Hermes must also trust /work/payments before the Skill becomes available.
```

The exposure result reports `hermes-project-trust`. Creating the symlink does not update Hermes project trust automatically.

### Reuse one canonical Skill across hosts

```text
$HOME/SkillsLibrary/skills/example
├── referenced by $HOME/.codex/skills/example
├── referenced by $HOME/.claude/skills/example
└── referenced by $HOME/.openclaw/skills/example
```

Updating the canonical directory updates what all three links resolve to; their installation records and removal choices remain independent.

### Reuse identical content for another host

```text
User: Install database-tools for Claude Code. I may already have installed it in Codex.
Skills Manager: The incoming content matches the canonical database-tools copy. I will not copy or replace it. Should Claude Code use it user-wide or only in a project?
User: User-wide.
```

Skills Manager creates or verifies only `$HOME/.claude/skills/database-tools`. The Codex installation and the one canonical directory remain unchanged.

### Choose between different versions

```text
User: Install this database-tools update for OpenClaw.
Skills Manager: The versions differ materially.
- Library-only: preserves the legacy export mode.
- Incoming-only: adds retry handling and a new validation step.
Recommendation: cancel and use `skill-creator` to merge the valuable unique behavior.
Impact: using incoming would switch the existing Codex global and OpenClaw shared installations together.
Choose use existing, use incoming, or cancel.
User: Use incoming.
```

Skills Manager dry-runs the exact `adopt <source> --replace` operation, applies it immediately because **use incoming** already resolved the version choice, validates the new canonical content and all previously correct links, then deletes the transaction-only rollback copy.

### Expose a group without partial installation

```text
User: Install the backend group in my Codex payments project.
Skills Manager: The backend group contains fastapi, postgres, and redis. I will preflight all three canonical Skills and links under /work/payments/.agents/skills before changing anything.
```

One conflict stops the normal group transaction before any member is linked. A conflict-free `group expose` is applied immediately, creates one flat symlink per member, and leaves the group manifest in the Library.

### Uninstall from one host without deleting the Skill

```text
User: Uninstall database-tools from Claude Code, but keep it in Codex.
Skills Manager: I will remove only the selected Claude Code exposure and installation record. The canonical copy and Codex installation will remain unchanged.
```

Skills Manager dry-runs `unexpose` for the exact Claude Code host, scope, and destination. Canonical removal remains a separate operation and is refused while any installation or group membership remains.

### Shared project discovery

```text
/work/payments/.agents/skills/database-tools
    -> $HOME/SkillsLibrary/skills/database-tools
```

Codex installed this project entry. OpenClaw or Hermes running in `/work/payments` can discover it when their runtime trust and eligibility checks allow it, without a second registration. The same agents running elsewhere do not discover that project entry.

### Preinstall before a project exists

```text
User: Install database-tools for a future Hermes project, but I do not have the project directory yet.
Skills Manager: I will keep the canonical copy, record a Hermes project installation without an exposure, and create no project directory.
```

### Migrate a legacy entry

```text
Skills Manager: This Skill is marked legacy global, which does not identify a host. Should it become Codex user-wide, Claude Code user-wide, OpenClaw shared, Hermes profile-wide, or remain legacy?
```

The selected mapping is dry-run and applied immediately when the result is conflict-free. The mapping choice itself is not reconfirmed.

## 12. Current limitations

- Only Codex, Claude Code, OpenClaw, and Hermes have explicit host adapters.
- Runtime discovery can still depend on client version, trust, eligibility, and workspace configuration.
- There is no Hook-based interception of external installation or overlap-check bypasses.
- Plugins, bundled system Skills, administrator Skills, and plugin caches are not managed.
- Nested groups, automatic updates, automatic Git initialization, and permanent deletion are not supported.
- Directory symlinks are required; there is no silent copy fallback.
- Skills Manager reports but does not automatically satisfy OpenClaw symlink-target allowlists or Hermes project trust.
- Skills Manager records explicit installations but does not suppress additional project Skills that a runtime discovers through its own compatible search paths.
