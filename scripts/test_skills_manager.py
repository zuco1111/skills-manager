#!/usr/bin/env python3
"""Isolated regression tests for skills_manager.py."""

from __future__ import annotations

import io
import importlib.util
import json
import os
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


SCRIPT_PATH = Path(__file__).with_name("skills_manager.py")
SKILL_ROOT = SCRIPT_PATH.parent.parent
SPEC = importlib.util.spec_from_file_location("skills_manager_under_test", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load {SCRIPT_PATH}")
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


class SkillsManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="skills-manager-test-")
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.home_patch = mock.patch.object(manager.Path, "home", return_value=self.home)
        self.home_patch.start()
        self.env_patch = mock.patch.dict(
            os.environ,
            {
                "CODEX_HOME": str(self.home / ".codex"),
                "OPENCLAW_STATE_DIR": str(self.home / ".openclaw"),
                "HERMES_HOME": str(self.home / ".hermes"),
            },
            clear=False,
        )
        self.env_patch.start()
        self.which_patch = mock.patch.object(manager.shutil, "which", return_value=None)
        self.which_patch.start()
        self.claude_candidates_patch = mock.patch.object(
            manager,
            "claude_code_executable_candidates",
            return_value=(self.home / ".local" / "bin" / "claude",),
        )
        self.claude_candidates_patch.start()
        self.original_global_skills_dir = manager.GLOBAL_SKILLS_DIR
        manager.GLOBAL_SKILLS_DIR = self.home / ".agents" / "skills"
        self.lib = manager.Library(self.root / "library")
        self.lib.ensure_layout()

    def tearDown(self) -> None:
        manager.GLOBAL_SKILLS_DIR = self.original_global_skills_dir
        self.claude_candidates_patch.stop()
        self.which_patch.stop()
        self.env_patch.stop()
        self.home_patch.stop()
        self.temp_dir.cleanup()

    def create_skill(self, name: str, description: str = "A valid test Skill.") -> Path:
        path = self.lib.skills / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n# Test Skill\n",
            encoding="utf-8",
        )
        return path

    def create_candidate(
        self,
        directory: str,
        name: str,
        description: str = "A valid candidate Skill.",
    ) -> Path:
        path = self.root / "candidates" / directory
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n# Candidate Skill\n",
            encoding="utf-8",
        )
        return path

    def fail_state_save(self, state: dict[str, object]) -> None:
        raise OSError("injected state write failure")

    def capture_json(self, function: object, args: Namespace) -> dict[str, object]:
        output = io.StringIO()
        with redirect_stdout(output):
            function(args, self.lib)  # type: ignore[operator]
        return json.loads(output.getvalue())

    def test_status_exposes_inputs_for_first_run_initialization_prompt(self) -> None:
        payload = self.capture_json(manager.cmd_status, Namespace())

        self.assertFalse(payload["canonical_manager_valid"])
        self.assertNotEqual(payload["global_manager_link_status"], "already-correct")
        self.assertEqual(
            payload["overlap"],
            {"enabled": True, "initial_scan_done": False},
        )

    def test_status_reports_initialized_manager_as_ready(self) -> None:
        target = self.create_skill("skills-manager")
        link = manager.GLOBAL_SKILLS_DIR / "skills-manager"
        link.parent.mkdir(parents=True)
        os.symlink(target, link, target_is_directory=True)

        payload = self.capture_json(manager.cmd_status, Namespace())

        self.assertTrue(payload["canonical_manager_valid"])
        self.assertEqual(payload["global_manager_link_status"], "already-correct")
        self.assertEqual(payload["manager_bootstrap"]["status"], "already-correct")
        self.assertFalse(payload["claude_compatibility_offer"])
        self.assertFalse(payload["claude_compatibility"]["offer"])

    def test_status_rejects_a_symlink_as_the_canonical_manager(self) -> None:
        external = self.root / "external" / "skills-manager"
        external.mkdir(parents=True)
        (external / "SKILL.md").write_text(
            "---\nname: skills-manager\ndescription: Manage test Skills.\n---\n",
            encoding="utf-8",
        )
        canonical = self.lib.skill_path("skills-manager")
        os.symlink(external, canonical, target_is_directory=True)
        global_link = manager.GLOBAL_SKILLS_DIR / "skills-manager"
        global_link.parent.mkdir(parents=True)
        os.symlink(external, global_link, target_is_directory=True)

        payload = self.capture_json(manager.cmd_status, Namespace())

        self.assertFalse(payload["canonical_manager_valid"])

    def test_initialize_command_and_legacy_alias_share_implementation(self) -> None:
        parser = manager.build_parser()

        initialize = parser.parse_args(["initialize"])
        legacy = parser.parse_args(manager.normalize_cli_args(["bootstrap"]))

        self.assertIs(initialize.func, manager.cmd_initialize_manager)
        self.assertIs(legacy.func, manager.cmd_initialize_manager)

    def test_help_shows_initialization_and_shared_bootstrap_terminology(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            manager.build_parser().print_help()

        help_text = output.getvalue()
        self.assertIn("initialize", help_text)
        self.assertIn("shared Skills Manager bootstrap", help_text)
        self.assertIn("Adopt, reuse, or compare", help_text)

    def test_skill_metadata_routing_and_examples_cover_management_model(self) -> None:
        metadata = manager.read_skill_metadata(SKILL_ROOT)
        description = metadata["description"]
        guide = (SKILL_ROOT / "references" / "user-guide.md").read_text(encoding="utf-8")
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        ui_text = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")

        for phrase in (
            "install or uninstall",
            "reuse an installed Skill",
            "duplicate or conflicting versions",
            "low-level installers only to fetch or stage sources",
        ):
            self.assertIn(phrase, description)
        self.assertIn("Read sections 1–4", skill_text)
        self.assertIn("Read sections 11–12", skill_text)
        self.assertIn("Host executable detection is not a prerequisite", skill_text)
        self.assertIn("one `--host` per approved host", skill_text)
        self.assertIn("dry-run `repair <skill>", skill_text)
        self.assertNotIn("Read [references/user-guide.md](references/user-guide.md) completely", skill_text)
        normal_sequence = guide.split("The normal sequence is:", 1)[1].split(
            "This sequence applies", 1
        )[0]
        self.assertLess(
            normal_sequence.index("Dry-run `adopt <source>`"),
            normal_sequence.index("functional overlap"),
        )
        for heading in (
            "### Reuse identical content for another host",
            "### Choose between different versions",
            "### Expose a group without partial installation",
            "### Uninstall from one host without deleting the Skill",
        ):
            self.assertIn(heading, guide)
        self.assertIn("$skills-manager", ui_text)
        self.assertIn("reusing its canonical copy", ui_text)
        for command in (
            "group list",
            "group create",
            "group add",
            "group remove",
            "group rename",
            "group delete",
            "group expose",
        ):
            self.assertIn(f"`{command}", guide)

    def test_overlap_help_describes_repeatable_candidates_and_review_boundary(self) -> None:
        parser = manager.build_parser()
        overlap = next(
            action for action in parser._actions if getattr(action, "dest", None) == "command"
        ).choices["overlap"]
        scan = next(
            action
            for action in overlap._actions
            if getattr(action, "dest", None) == "overlap_command"
        ).choices["scan"]
        output = io.StringIO()

        with redirect_stdout(output):
            scan.print_help()

        help_text = output.getvalue()
        self.assertIn("--candidate", help_text)
        self.assertIn("Repeat --candidate", help_text)
        self.assertIn("not semantic overlap determinations", help_text)

    def test_overlap_parser_binds_all_subcommands_and_repeatable_candidates(self) -> None:
        parser = manager.build_parser()

        scan = parser.parse_args(
            ["overlap", "scan", "--candidate", "first", "--candidate", "second"]
        )
        setting = parser.parse_args(["overlap", "set", "off", "--apply"])
        mark = parser.parse_args(["overlap", "mark-initial-scan", "--apply"])

        self.assertIs(scan.func, manager.cmd_overlap_scan)
        self.assertEqual(scan.candidate, ["first", "second"])
        self.assertIs(setting.func, manager.cmd_overlap_set)
        self.assertEqual(setting.setting, "off")
        self.assertTrue(setting.apply)
        self.assertIs(mark.func, manager.cmd_overlap_mark_initial_scan)
        self.assertTrue(mark.apply)

    def test_host_aware_parser_preserves_legacy_and_accepts_all_target_options(self) -> None:
        parser = manager.build_parser()

        legacy = parser.parse_args(["expose", "demo", "--scope", "global"])
        codex = parser.parse_args(
            ["expose", "demo", "--host", "codex", "--scope", "project", "--project", "/tmp"]
        )
        openclaw = parser.parse_args(
            [
                "expose",
                "demo",
                "--host",
                "openclaw",
                "--scope",
                "agent",
                "--workspace",
                "/tmp",
            ]
        )
        initialize = parser.parse_args(["initialize", "--host", "hermes"])

        self.assertIsNone(legacy.host)
        self.assertEqual(codex.host, "codex")
        self.assertEqual(codex.project, "/tmp")
        self.assertEqual(openclaw.host, "openclaw")
        self.assertEqual(openclaw.workspace, "/tmp")
        self.assertEqual(initialize.host, "hermes")

    def test_overlap_state_defaults_and_old_state_upgrade(self) -> None:
        self.assertEqual(
            self.lib.load_state()["overlap"],
            {"enabled": True, "initial_scan_done": False},
        )
        legacy_state = {
            "schema_version": manager.SCHEMA_VERSION,
            "migration_status": "declined",
            "exposures": {},
            "skill_scopes": {},
            "backups": [],
        }
        self.lib.state_file.write_text(json.dumps(legacy_state), encoding="utf-8")

        upgraded = self.lib.load_state()

        self.assertEqual(upgraded["migration_status"], "declined")
        self.assertEqual(
            upgraded["overlap"],
            {"enabled": True, "initial_scan_done": False},
        )
        self.assertEqual(upgraded["host_model_version"], manager.HOST_MODEL_VERSION)
        self.assertEqual(upgraded["installations"], {})

    def test_legacy_state_status_is_read_only_and_reports_pending_bindings(self) -> None:
        skill = self.create_skill("legacy-skill")
        legacy_link = manager.GLOBAL_SKILLS_DIR / "legacy-skill"
        legacy_link.parent.mkdir(parents=True)
        os.symlink(skill, legacy_link, target_is_directory=True)
        legacy_state = {
            "schema_version": manager.SCHEMA_VERSION,
            "migration_status": "completed",
            "exposures": {
                str(legacy_link): {
                    "skill": "legacy-skill",
                    "target": str(skill),
                    "scope": "global",
                    "project": None,
                }
            },
            "skill_scopes": {"legacy-skill": "global"},
            "backups": [],
            "overlap": {"enabled": True, "initial_scan_done": True},
        }
        self.lib.state_file.write_text(json.dumps(legacy_state), encoding="utf-8")
        before = self.lib.state_file.read_bytes()

        payload = self.capture_json(manager.cmd_status, Namespace())

        self.assertEqual(payload["legacy_bindings_pending"], ["legacy-skill"])
        self.assertEqual(payload["installations"], {})
        self.assertEqual(self.lib.state_file.read_bytes(), before)

    def test_status_reserves_only_the_exact_manager_bootstrap(self) -> None:
        target = self.create_skill("skills-manager")
        bootstrap = manager.GLOBAL_SKILLS_DIR / "skills-manager"
        bootstrap.parent.mkdir(parents=True)
        os.symlink(target, bootstrap, target_is_directory=True)
        state = self.lib.load_state()
        state["skill_scopes"]["skills-manager"] = "global"
        manager.record_exposure(
            state, bootstrap, target, "skills-manager", "global", None
        )
        self.lib.save_state(state)

        ready = self.capture_json(manager.cmd_status, Namespace())

        self.assertNotIn("skills-manager", ready["legacy_bindings_pending"])

        project = self.root / "project"
        project.mkdir()
        extra = project / ".agents" / "skills" / "skills-manager"
        extra.parent.mkdir(parents=True)
        os.symlink(target, extra, target_is_directory=True)
        state = self.lib.load_state()
        manager.record_exposure(
            state, extra, target, "skills-manager", "project", project
        )
        self.lib.save_state(state)

        unexpected = self.capture_json(manager.cmd_status, Namespace())

        self.assertIn("skills-manager", unexpected["legacy_bindings_pending"])

    def test_discover_excludes_exact_manager_bootstrap_but_keeps_other_legacy_skills(self) -> None:
        manager_target = self.create_skill("skills-manager")
        ordinary_target = self.create_skill("legacy-visible")
        manager_link = manager.GLOBAL_SKILLS_DIR / "skills-manager"
        ordinary_link = manager.GLOBAL_SKILLS_DIR / "legacy-visible"
        manager_link.parent.mkdir(parents=True)
        os.symlink(manager_target, manager_link, target_is_directory=True)
        os.symlink(ordinary_target, ordinary_link, target_is_directory=True)

        payload = self.capture_json(
            manager.cmd_discover,
            Namespace(host=None, include_legacy=False, project=None, workspace=None),
        )

        paths = {item["path"] for item in payload["candidates"]}
        self.assertNotIn(str(manager_link), paths)
        self.assertIn(str(ordinary_link), paths)

    def test_discover_scans_only_explicitly_approved_host_and_roots(self) -> None:
        codex_skill = self.home / ".codex" / "skills" / "codex-only"
        claude_skill = self.home / ".claude" / "skills" / "claude-only"
        legacy_skill = manager.GLOBAL_SKILLS_DIR / "legacy-only"
        for path, name in (
            (codex_skill, "codex-only"),
            (claude_skill, "claude-only"),
            (legacy_skill, "legacy-only"),
        ):
            path.mkdir(parents=True)
            (path / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: A discoverable Skill.\n---\n",
                encoding="utf-8",
            )

        payload = self.capture_json(
            manager.cmd_discover,
            Namespace(
                host=["codex"],
                include_legacy=False,
                project=None,
                workspace=None,
            ),
        )

        self.assertEqual(payload["hosts"], ["codex"])
        self.assertFalse(payload["include_legacy"])
        paths = {item["path"] for item in payload["candidates"]}
        self.assertEqual(paths, {str(codex_skill)})
        self.assertEqual(
            payload["roots"],
            [{"scope": "codex-global", "path": str(self.home / ".codex" / "skills")}],
        )

    def test_discover_requires_openclaw_for_workspace_root(self) -> None:
        workspace = self.root / "agent-workspace"
        workspace.mkdir()

        with self.assertRaisesRegex(manager.ManagerError, "requires openclaw"):
            manager.cmd_discover(
                Namespace(
                    host=["codex"],
                    include_legacy=False,
                    project=None,
                    workspace=[str(workspace)],
                ),
                self.lib,
            )

    def test_validator_accepts_agent_skills_compatibility_field(self) -> None:
        skill = self.lib.skills / "portable-skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\n"
            "name: portable-skill\n"
            "description: A portable test Skill.\n"
            "compatibility: Requires Python 3.11 or newer.\n"
            "---\n",
            encoding="utf-8",
        )

        result = manager.validate_skill(skill)

        self.assertTrue(result["valid"], result["errors"])

    def test_overlap_controls_are_dry_run_then_apply(self) -> None:
        dry_set = self.capture_json(
            manager.cmd_overlap_set, Namespace(setting="off", apply=False)
        )
        self.assertFalse(dry_set["new"])
        self.assertTrue(self.lib.load_state()["overlap"]["enabled"])

        applied_set = self.capture_json(
            manager.cmd_overlap_set, Namespace(setting="off", apply=True)
        )
        self.assertTrue(applied_set["apply"])
        self.assertFalse(self.lib.load_state()["overlap"]["enabled"])

        dry_mark = self.capture_json(
            manager.cmd_overlap_mark_initial_scan, Namespace(apply=False)
        )
        self.assertTrue(dry_mark["new"])
        self.assertFalse(self.lib.load_state()["overlap"]["initial_scan_done"])

        applied_mark = self.capture_json(
            manager.cmd_overlap_mark_initial_scan, Namespace(apply=True)
        )
        self.assertTrue(applied_mark["apply"])
        self.assertTrue(self.lib.load_state()["overlap"]["initial_scan_done"])

    def test_overlap_scan_is_skipped_when_disabled(self) -> None:
        state = self.lib.load_state()
        state["overlap"]["enabled"] = False
        self.lib.save_state(state)

        payload = self.capture_json(
            manager.cmd_overlap_scan,
            Namespace(candidate=[str(self.root / "missing-candidate")]),
        )

        self.assertTrue(payload["skipped"])
        self.assertEqual(payload["mode"], "disabled")
        self.assertEqual(payload["lexical_candidates"], [])
        self.assertEqual(payload["summary"]["pairs_considered"], 0)

    def test_overlap_scan_without_candidates_screens_canonical_pairs_with_context(self) -> None:
        first = self.create_skill(
            "document-format",
            "Format and polish document layouts for publishing.",
        )
        self.create_skill(
            "document-layout",
            "Review document layouts and formatting for publication.",
        )
        state = self.lib.load_state()
        state["skill_scopes"]["document-format"] = "project"
        manager.record_exposure(
            state,
            self.root / "project" / ".agents" / "skills" / "document-format",
            first,
            "document-format",
            "project",
            self.root / "project",
        )
        self.lib.save_state(state)
        manager.atomic_write(
            self.lib.group_path("publishing"),
            manager.group_text("publishing", ["document-format"]),
        )
        global_link = manager.GLOBAL_SKILLS_DIR / "document-format"
        global_link.parent.mkdir(parents=True)
        os.symlink(first, global_link, target_is_directory=True)

        payload = self.capture_json(manager.cmd_overlap_scan, Namespace(candidate=[]))

        self.assertEqual(payload["mode"], "canonical-pairs")
        self.assertEqual(payload["summary"]["pairs_considered"], 1)
        self.assertEqual(len(payload["lexical_candidates"]), 1)
        pair = payload["lexical_candidates"][0]
        existing = next(item for item in (pair["left"], pair["right"]) if item["name"] == "document-format")
        self.assertEqual(existing["scope"], "project")
        self.assertEqual(existing["group_memberships"], ["publishing"])
        self.assertEqual(len(existing["exposures"]), 2)
        self.assertIn(str(global_link), {item["link"] for item in existing["exposures"]})
        self.assertIn("document", pair["shared_terms"])
        self.assertTrue(pair["signals"])

    def test_overlap_scan_filters_generic_manager_vocabulary(self) -> None:
        self.create_skill(
            "server-manager",
            "Inspect API server status and update network services.",
        )
        self.create_skill(
            "credential-manager",
            "Store API keys and manage local service credentials.",
        )

        payload = self.capture_json(manager.cmd_overlap_scan, Namespace(candidate=[]))

        self.assertEqual(payload["lexical_candidates"], [])
        self.assertEqual(payload["summary"]["filtered_out"], 1)

    def test_overlap_scan_uses_only_frontmatter_and_is_read_only(self) -> None:
        first = self.create_skill(
            "invoice-builder",
            "Generate billing invoices for customers.",
        )
        second = self.create_skill(
            "weather-monitor",
            "Forecast rainfall and temperature conditions.",
        )
        shared_body = "\n# Shared body\n\nIdentical implementation details must not affect overlap.\n"
        for skill in (first, second):
            frontmatter = (skill / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
            (skill / "SKILL.md").write_text(
                f"---{frontmatter}---{shared_body}",
                encoding="utf-8",
            )
        state_before = self.lib.state_file.read_bytes()
        files_before = [(skill / "SKILL.md").read_bytes() for skill in (first, second)]

        payload = self.capture_json(manager.cmd_overlap_scan, Namespace(candidate=[]))

        self.assertEqual(payload["lexical_candidates"], [])
        self.assertEqual(payload["summary"]["filtered_out"], 1)
        self.assertEqual(self.lib.state_file.read_bytes(), state_before)
        self.assertEqual(
            [(skill / "SKILL.md").read_bytes() for skill in (first, second)],
            files_before,
        )

    def test_overlap_scan_finds_ppt_beautify_vs_optimize_as_review_candidate(self) -> None:
        self.create_skill(
            "ppt-beautify",
            "Beautify PowerPoint presentations with polished visual layouts.",
        )
        candidate = self.create_candidate(
            "ppt-optimize-source",
            "ppt-optimize",
            "Optimize PowerPoint presentation visuals and slide layouts.",
        )

        payload = self.capture_json(
            manager.cmd_overlap_scan, Namespace(candidate=[str(candidate)])
        )

        self.assertEqual(payload["mode"], "candidates")
        self.assertEqual(len(payload["lexical_candidates"]), 1)
        pair = payload["lexical_candidates"][0]
        self.assertEqual(
            {pair["left"]["name"], pair["right"]["name"]},
            {"ppt-beautify", "ppt-optimize"},
        )
        self.assertIn("ppt", pair["shared_terms"])
        self.assertIn("semantic review", payload["semantic_review_notice"])
        self.assertIn("do not determine", payload["semantic_review_notice"])

    def test_overlap_scan_skips_same_target_without_reporting_a_name_conflict(self) -> None:
        canonical = self.create_skill("same-target", "Process presentation layouts.")

        payload = self.capture_json(
            manager.cmd_overlap_scan, Namespace(candidate=[str(canonical)])
        )

        self.assertEqual(payload["lexical_candidates"], [])
        self.assertEqual(len(payload["skipped_same_target"]), 1)
        self.assertEqual(payload["same_name"], [])

    def test_overlap_scan_reports_same_name_separately(self) -> None:
        self.create_skill("duplicate-name", "Create presentation layouts.")
        candidate = self.create_candidate(
            "different-source", "duplicate-name", "Optimize presentation layouts."
        )

        payload = self.capture_json(
            manager.cmd_overlap_scan, Namespace(candidate=[str(candidate)])
        )

        self.assertEqual(len(payload["same_name"]), 1)
        self.assertEqual(payload["skipped_same_target"], [])
        self.assertEqual(payload["lexical_candidates"], [])

    def test_overlap_scan_compares_multiple_candidates_to_canonical_and_each_other(self) -> None:
        self.create_skill("document-tools", "Edit document formats and visual layouts.")
        first = self.create_candidate(
            "convert-source",
            "document-convert",
            "Convert document formats while preserving layouts.",
        )
        second = self.create_candidate(
            "layout-source",
            "document-layout",
            "Optimize document layouts and common formats.",
        )

        payload = self.capture_json(
            manager.cmd_overlap_scan,
            Namespace(candidate=[str(first), str(second)]),
        )

        self.assertEqual(payload["summary"]["pairs_considered"], 3)
        self.assertEqual(len(payload["lexical_candidates"]), 3)
        self.assertTrue(
            any(
                pair["left"]["kind"] == pair["right"]["kind"] == "candidate"
                for pair in payload["lexical_candidates"]
            )
        )

    def test_initialize_dry_run_then_apply(self) -> None:
        source = self.root / "download" / "skills-manager"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "---\n"
            "name: skills-manager\n"
            "description: Manage test Skills.\n"
            "---\n\n"
            "# Skills Manager\n",
            encoding="utf-8",
        )

        dry_run = self.capture_json(
            manager.cmd_initialize_manager,
            Namespace(source=str(source), apply=False),
        )

        self.assertEqual(dry_run["action"], "initialize-manager")
        self.assertEqual(dry_run["host"], "bootstrap")
        self.assertIsNone(dry_run["requested_host"])
        self.assertFalse(dry_run["apply"])
        self.assertFalse(dry_run["claude_compatibility"]["requested"])
        self.assertTrue(source.is_dir())
        self.assertFalse(self.lib.skill_path("skills-manager").exists())

        applied = self.capture_json(
            manager.cmd_initialize_manager,
            Namespace(source=str(source), apply=True),
        )

        target = self.lib.skill_path("skills-manager")
        link = manager.GLOBAL_SKILLS_DIR / "skills-manager"
        self.assertTrue(applied["apply"])
        self.assertTrue(target.is_dir())
        self.assertFalse(source.exists())
        self.assertTrue(Path(applied["backup"]).is_dir())
        self.assertTrue(link.is_symlink())
        self.assertEqual(manager.resolved(link), manager.resolved(target))
        self.assertEqual(self.lib.load_state()["skill_scopes"]["skills-manager"], "global")

        repeated = self.capture_json(
            manager.cmd_initialize_manager,
            Namespace(source=str(source), apply=True),
        )

        self.assertEqual(repeated["result"], "already-canonical")
        self.assertTrue(repeated["apply"])
        self.assertTrue(target.is_dir())
        self.assertTrue(link.is_symlink())

    def test_host_exposure_path_matrix(self) -> None:
        project = self.root / "project"
        workspace = self.root / "openclaw-workspace"
        project.mkdir()
        workspace.mkdir()

        cases = [
            ("codex", "global", None, None, self.home / ".codex" / "skills" / "demo"),
            ("codex", "project", str(project), None, project / ".agents" / "skills" / "demo"),
            ("claude-code", "global", None, None, self.home / ".claude" / "skills" / "demo"),
            ("claude-code", "project", str(project), None, project / ".claude" / "skills" / "demo"),
            ("openclaw", "global", None, None, self.home / ".openclaw" / "skills" / "demo"),
            ("openclaw", "agent", None, str(workspace), workspace / "skills" / "demo"),
            ("hermes", "global", None, None, self.home / ".hermes" / "skills" / "demo"),
            ("hermes", "project", str(project), None, project / ".hermes" / "skills" / "demo"),
        ]
        for host, scope, project_value, workspace_value, expected in cases:
            with self.subTest(host=host, scope=scope):
                link, _ = manager.scope_link(
                    "demo",
                    scope,
                    project_value,
                    host,
                    workspace=workspace_value,
                )
                self.assertEqual(link, expected)

        openclaw_custom, _ = manager.scope_link(
            "demo", "global", None, "openclaw", state_dir=str(self.root / "oc-state")
        )
        hermes_custom, _ = manager.scope_link(
            "demo", "global", None, "hermes", profile_home=str(self.root / "hermes-profile")
        )
        self.assertEqual(openclaw_custom, self.root / "oc-state" / "skills" / "demo")
        self.assertEqual(hermes_custom, self.root / "hermes-profile" / "skills" / "demo")

    def test_non_claude_initialize_keeps_only_the_shared_bootstrap(self) -> None:
        target = self.create_skill("skills-manager")
        legacy_link = manager.GLOBAL_SKILLS_DIR / "skills-manager"
        legacy_link.parent.mkdir(parents=True)
        os.symlink(target, legacy_link, target_is_directory=True)
        state = self.lib.load_state()
        state["skill_scopes"]["skills-manager"] = "global"
        manager.record_exposure(
            state, legacy_link, target, "skills-manager", "global", None
        )
        self.lib.save_state(state)

        for host in ("codex", "openclaw", "hermes"):
            with self.subTest(host=host):
                payload = self.capture_json(
                    manager.cmd_initialize_manager,
                    Namespace(
                        source=None,
                        host=host,
                        state_dir=None,
                        profile_home=None,
                        apply=True,
                    ),
                )
                native_link = manager.default_host_root(host) / "skills" / "skills-manager"
                self.assertEqual(payload["host"], "bootstrap")
                self.assertEqual(payload["requested_host"], host)
                self.assertFalse(native_link.exists())
        self.assertTrue(legacy_link.is_symlink())
        state = self.lib.load_state()
        self.assertIn("skills-manager", state["skill_scopes"])
        self.assertNotIn("skills-manager", state["installations"])

    def test_claude_initialize_creates_bootstrap_and_compatibility_atomically(self) -> None:
        source = self.root / "download" / "skills-manager"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "---\nname: skills-manager\ndescription: Manage test Skills.\n---\n",
            encoding="utf-8",
        )

        dry_run = self.capture_json(
            manager.cmd_initialize_manager,
            Namespace(
                source=str(source),
                host="claude-code",
                state_dir=None,
                profile_home=None,
                apply=False,
            ),
        )

        bootstrap = manager.GLOBAL_SKILLS_DIR / "skills-manager"
        claude_link = self.home / ".claude" / "skills" / "skills-manager"
        self.assertTrue(dry_run["claude_compatibility"]["requested"])
        self.assertFalse(bootstrap.exists())
        self.assertFalse(claude_link.exists())

        applied = self.capture_json(
            manager.cmd_initialize_manager,
            Namespace(
                source=str(source),
                host="claude-code",
                state_dir=None,
                profile_home=None,
                apply=True,
            ),
        )

        target = self.lib.skill_path("skills-manager")
        self.assertTrue(bootstrap.is_symlink())
        self.assertTrue(claude_link.is_symlink())
        self.assertEqual(manager.resolved(bootstrap), manager.resolved(target))
        self.assertEqual(manager.resolved(claude_link), manager.resolved(target))
        self.assertEqual(applied["claude_compatibility"]["status"], "already-correct")
        records = self.lib.load_state()["installations"]["skills-manager"]
        self.assertEqual({item["host"] for item in records}, {"claude-code"})

    def test_non_claude_initialize_offers_detected_claude_without_creating_it(self) -> None:
        target = self.create_skill("skills-manager")
        with mock.patch.object(manager.shutil, "which", return_value="/usr/local/bin/claude"):
            payload = self.capture_json(
                manager.cmd_initialize_manager,
                Namespace(
                    source=None,
                    host="openclaw",
                    state_dir=None,
                    profile_home=None,
                    apply=True,
                ),
            )

        claude_link = self.home / ".claude" / "skills" / "skills-manager"
        self.assertTrue(payload["claude_compatibility"]["detected"])
        self.assertTrue(payload["claude_compatibility"]["offer"])
        self.assertFalse(claude_link.exists())
        self.assertTrue((manager.GLOBAL_SKILLS_DIR / "skills-manager").is_symlink())
        self.assertEqual(
            manager.resolved(manager.GLOBAL_SKILLS_DIR / "skills-manager"),
            manager.resolved(target),
        )

    def test_claude_detection_checks_native_launcher_when_path_is_incomplete(self) -> None:
        launcher = self.home / ".local" / "bin" / "claude"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("#!/bin/sh\n", encoding="utf-8")
        launcher.chmod(0o755)

        self.assertTrue(manager.claude_code_detected())

    def test_claude_initialize_rolls_back_both_links_when_state_save_fails(self) -> None:
        source = self.root / "download" / "skills-manager"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "---\nname: skills-manager\ndescription: Manage test Skills.\n---\n",
            encoding="utf-8",
        )
        self.lib.save_state = self.fail_state_save

        with self.assertRaisesRegex(OSError, "injected state write failure"):
            manager.cmd_initialize_manager(
                Namespace(
                    source=str(source),
                    host="claude-code",
                    state_dir=None,
                    profile_home=None,
                    apply=True,
                ),
                self.lib,
            )

        self.assertTrue(source.is_dir())
        self.assertFalse(self.lib.skill_path("skills-manager").exists())
        self.assertFalse((manager.GLOBAL_SKILLS_DIR / "skills-manager").exists())
        self.assertFalse((self.home / ".claude" / "skills" / "skills-manager").exists())
        self.assertEqual(list(self.lib.backups.iterdir()), [])

    def test_claude_initialize_preflights_conflict_before_any_mutation(self) -> None:
        source = self.root / "download" / "skills-manager"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "---\nname: skills-manager\ndescription: Manage test Skills.\n---\n",
            encoding="utf-8",
        )
        claude_link = self.home / ".claude" / "skills" / "skills-manager"
        claude_link.mkdir(parents=True)
        before = self.lib.state_file.read_bytes()

        with self.assertRaisesRegex(manager.ManagerError, "compatibility entry conflict"):
            manager.cmd_initialize_manager(
                Namespace(
                    source=str(source),
                    host="claude-code",
                    state_dir=None,
                    profile_home=None,
                    apply=True,
                ),
                self.lib,
            )

        self.assertTrue(source.is_dir())
        self.assertTrue(claude_link.is_dir())
        self.assertFalse(self.lib.skill_path("skills-manager").exists())
        self.assertFalse((manager.GLOBAL_SKILLS_DIR / "skills-manager").exists())
        self.assertEqual(self.lib.state_file.read_bytes(), before)

    def test_claude_initialize_can_adopt_the_native_entry_as_its_source(self) -> None:
        source = self.home / ".claude" / "skills" / "skills-manager"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "---\nname: skills-manager\ndescription: Manage test Skills.\n---\n",
            encoding="utf-8",
        )

        payload = self.capture_json(
            manager.cmd_initialize_manager,
            Namespace(
                source=str(source),
                host="claude-code",
                state_dir=None,
                profile_home=None,
                apply=True,
            ),
        )

        target = self.lib.skill_path("skills-manager")
        self.assertTrue(payload["apply"])
        self.assertTrue(source.is_symlink())
        self.assertTrue((manager.GLOBAL_SKILLS_DIR / "skills-manager").is_symlink())
        self.assertEqual(manager.resolved(source), manager.resolved(target))
        self.assertTrue(Path(payload["backup"]).is_dir())

    def test_initialize_repoints_a_bootstrap_that_targets_the_active_source(self) -> None:
        source = self.root / "download" / "skills-manager"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "---\nname: skills-manager\ndescription: Manage test Skills.\n---\n",
            encoding="utf-8",
        )
        bootstrap = manager.GLOBAL_SKILLS_DIR / "skills-manager"
        bootstrap.parent.mkdir(parents=True)
        os.symlink(source, bootstrap, target_is_directory=True)

        payload = self.capture_json(
            manager.cmd_initialize_manager,
            Namespace(
                source=str(source),
                host="codex",
                state_dir=None,
                profile_home=None,
                apply=True,
            ),
        )

        target = self.lib.skill_path("skills-manager")
        self.assertTrue(payload["repoints_source_symlink"])
        self.assertFalse(source.exists())
        self.assertTrue(bootstrap.is_symlink())
        self.assertEqual(manager.resolved(bootstrap), manager.resolved(target))
        self.assertTrue(Path(payload["backup"]).is_dir())

    def test_initialize_restores_source_bootstrap_symlink_on_state_failure(self) -> None:
        source = self.root / "download" / "skills-manager"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "---\nname: skills-manager\ndescription: Manage test Skills.\n---\n",
            encoding="utf-8",
        )
        bootstrap = manager.GLOBAL_SKILLS_DIR / "skills-manager"
        bootstrap.parent.mkdir(parents=True)
        os.symlink(source, bootstrap, target_is_directory=True)
        original_link_text = os.readlink(bootstrap)
        self.lib.save_state = self.fail_state_save

        with self.assertRaisesRegex(OSError, "injected state write failure"):
            manager.cmd_initialize_manager(
                Namespace(
                    source=str(source),
                    host="codex",
                    state_dir=None,
                    profile_home=None,
                    apply=True,
                ),
                self.lib,
            )

        self.assertTrue(source.is_dir())
        self.assertTrue(bootstrap.is_symlink())
        self.assertEqual(os.readlink(bootstrap), original_link_text)
        self.assertEqual(manager.resolved(bootstrap), manager.resolved(source))
        self.assertFalse(self.lib.skill_path("skills-manager").exists())
        self.assertEqual(list(self.lib.backups.iterdir()), [])

    def test_claude_compatibility_can_be_removed_without_removing_bootstrap(self) -> None:
        self.create_skill("skills-manager")
        self.capture_json(
            manager.cmd_initialize_manager,
            Namespace(
                source=None,
                host="claude-code",
                state_dir=None,
                profile_home=None,
                apply=True,
            ),
        )

        payload = self.capture_json(
            manager.cmd_unexpose,
            Namespace(
                skill="skills-manager",
                host="claude-code",
                scope="global",
                project=None,
                workspace=None,
                state_dir=None,
                profile_home=None,
                apply=True,
            ),
        )

        self.assertTrue(payload["apply"])
        self.assertTrue((manager.GLOBAL_SKILLS_DIR / "skills-manager").is_symlink())
        self.assertFalse((self.home / ".claude" / "skills" / "skills-manager").exists())
        self.assertNotIn("skills-manager", self.lib.load_state()["installations"])

    def test_manager_generic_lifecycle_commands_require_initialize(self) -> None:
        source = self.create_candidate("manager-source", "skills-manager")

        with self.assertRaisesRegex(manager.ManagerError, "Use initialize"):
            manager.cmd_adopt(
                Namespace(source=str(source), replace=False, apply=False), self.lib
            )

        target = self.create_skill("skills-manager")
        update_plan = self.capture_json(
            manager.cmd_adopt,
            Namespace(source=str(source), replace=True, apply=False),
        )
        self.assertEqual(update_plan["action"], "replace")

        with self.assertRaisesRegex(manager.ManagerError, "Use initialize"):
            manager.cmd_expose(
                Namespace(skill="skills-manager"), self.lib
            )
        with self.assertRaisesRegex(manager.ManagerError, "Use initialize"):
            manager.cmd_migrate(
                Namespace(source=str(target)), self.lib
            )
        with self.assertRaisesRegex(manager.ManagerError, "reserved"):
            manager.cmd_remove(
                Namespace(skill="skills-manager", apply=False), self.lib
            )

    def test_adopt_reuses_identical_canonical_content_and_ignores_local_noise(self) -> None:
        target = self.create_skill("shared-skill")
        source = self.root / "incoming" / "shared-skill"
        manager.shutil.copytree(target, source)
        (source / ".git").write_text("gitdir: /tmp/worktree", encoding="utf-8")
        (source / ".DS_Store").write_bytes(b"finder metadata")
        (source / "__pycache__").mkdir()
        (source / "__pycache__" / "module.pyc").write_bytes(b"cache")
        (source / "ignored.pyc").write_bytes(b"cache")
        before_target = (target / "SKILL.md").read_bytes()
        before_state = self.lib.state_file.read_bytes()

        payload = self.capture_json(
            manager.cmd_adopt,
            Namespace(source=str(source), replace=True, apply=True),
        )

        self.assertEqual(payload["action"], "reuse-existing")
        self.assertEqual(payload["result"], "content-identical")
        self.assertFalse(payload["mutation"])
        self.assertTrue(payload["comparison"]["identical"])
        self.assertEqual((target / "SKILL.md").read_bytes(), before_target)
        self.assertEqual(self.lib.state_file.read_bytes(), before_state)
        self.assertTrue(source.is_dir())
        self.assertEqual(list(self.lib.backups.iterdir()), [])
        self.assertEqual(list(self.lib.staging.iterdir()), [])

        (source / ".git").unlink()
        (source / ".git").mkdir()
        (source / ".git" / "config").write_text("incoming-only", encoding="utf-8")
        directory_noise = manager.compare_skill_contents(target, source)
        self.assertTrue(directory_noise["identical"])
        manager.shutil.rmtree(source / ".git")
        (source / "required-empty-directory").mkdir()
        directory_change = manager.compare_skill_contents(target, source)
        self.assertFalse(directory_change["identical"])
        self.assertEqual(directory_change["added"], ["required-empty-directory"])

    def test_adopt_reports_version_choice_and_all_affected_hosts(self) -> None:
        target = self.create_skill("versioned-skill")
        source = self.create_candidate(
            "incoming-version", "versioned-skill", "A different incoming version."
        )
        for host in ("codex", "claude-code"):
            self.capture_json(
                manager.cmd_expose,
                Namespace(
                    skill="versioned-skill",
                    host=host,
                    scope="global",
                    project=None,
                    workspace=None,
                    state_dir=None,
                    profile_home=None,
                    apply=True,
                ),
            )
        before_target = (target / "SKILL.md").read_bytes()
        before_state = self.lib.state_file.read_bytes()

        payload = self.capture_json(
            manager.cmd_adopt,
            Namespace(source=str(source), replace=False, apply=True),
        )

        self.assertEqual(payload["action"], "version-choice-required")
        self.assertFalse(payload["apply"])
        self.assertFalse(payload["mutation"])
        self.assertFalse(payload["comparison"]["identical"])
        self.assertEqual(
            payload["installations"]["host_scopes"],
            ["claude-code:global", "codex:global"],
        )
        self.assertEqual(
            [choice["choice"] for choice in payload["choices"]],
            ["use-existing", "use-incoming", "cancel"],
        )
        incoming_choice = next(
            choice for choice in payload["choices"] if choice["choice"] == "use-incoming"
        )
        self.assertIn("repeat it immediately with --apply", incoming_choice["note"])
        self.assertIn("without another confirmation", incoming_choice["note"])
        self.assertEqual((target / "SKILL.md").read_bytes(), before_target)
        self.assertEqual(self.lib.state_file.read_bytes(), before_state)

    def test_adopt_replacement_deletes_transaction_rollback_after_validation(self) -> None:
        target = self.create_skill("replace-version")
        source = self.create_candidate(
            "replacement", "replace-version", "The approved replacement version."
        )
        links = []
        for host in ("codex", "claude-code"):
            self.capture_json(
                manager.cmd_expose,
                Namespace(
                    skill="replace-version",
                    host=host,
                    scope="global",
                    project=None,
                    workspace=None,
                    state_dir=None,
                    profile_home=None,
                    apply=True,
                ),
            )
            links.append(manager.scope_link("replace-version", "global", None, host)[0])

        payload = self.capture_json(
            manager.cmd_adopt,
            Namespace(source=str(source), replace=True, apply=True),
        )

        self.assertEqual(payload["action"], "replace")
        self.assertTrue(payload["rollback_backup_deleted"])
        self.assertIsNone(payload["backup"])
        self.assertIsNone(payload["cleanup_pending"])
        self.assertIn("approved replacement", (target / "SKILL.md").read_text(encoding="utf-8"))
        self.assertTrue(source.is_dir())
        for link in links:
            self.assertTrue(link.is_symlink())
            self.assertEqual(manager.resolved(link), manager.resolved(target))
        self.assertTrue(
            all(item["status"] == "already-correct" for item in payload["validation"]["links"])
        )
        self.assertEqual(list(self.lib.staging.iterdir()), [])
        self.assertEqual(list(self.lib.backups.iterdir()), [])
        self.assertEqual(self.lib.load_state()["backups"], [])

    def test_adopt_replacement_restores_old_version_when_state_save_fails(self) -> None:
        target = self.create_skill("replace-rollback", "The original version.")
        source = self.create_candidate(
            "replacement-rollback", "replace-rollback", "The incoming version."
        )
        original = (target / "SKILL.md").read_bytes()
        self.lib.save_state = self.fail_state_save

        with self.assertRaisesRegex(OSError, "injected state write failure"):
            manager.cmd_adopt(
                Namespace(source=str(source), replace=True, apply=True), self.lib
            )

        self.assertEqual((target / "SKILL.md").read_bytes(), original)
        self.assertTrue(source.is_dir())
        self.assertEqual(list(self.lib.staging.iterdir()), [])
        self.assertEqual(list(self.lib.backups.iterdir()), [])

    def test_adopt_reports_cleanup_pending_without_reverting_valid_replacement(self) -> None:
        target = self.create_skill("cleanup-pending", "The original version.")
        source = self.create_candidate(
            "cleanup-pending-source", "cleanup-pending", "The replacement version."
        )
        self.capture_json(
            manager.cmd_expose,
            Namespace(
                skill="cleanup-pending",
                host="codex",
                scope="global",
                project=None,
                workspace=None,
                state_dir=None,
                profile_home=None,
                apply=True,
            ),
        )
        original_rmtree = manager.shutil.rmtree

        def fail_rollback_cleanup(path: object, *args: object, **kwargs: object) -> None:
            if Path(path).name.startswith(".replace-rollback-v1-"):
                raise OSError("injected rollback cleanup failure")
            original_rmtree(path, *args, **kwargs)

        with mock.patch.object(manager.shutil, "rmtree", side_effect=fail_rollback_cleanup):
            payload = self.capture_json(
                manager.cmd_adopt,
                Namespace(source=str(source), replace=True, apply=True),
            )

        self.assertFalse(payload["rollback_backup_deleted"])
        self.assertIn("injected rollback cleanup failure", payload["cleanup_pending"]["error"])
        rollback = Path(payload["cleanup_pending"]["path"])
        self.assertTrue(rollback.is_dir())
        self.assertIn("replacement version", (target / "SKILL.md").read_text(encoding="utf-8"))

        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            manager.cmd_doctor(Namespace(), self.lib)
        doctor = json.loads(output.getvalue())
        self.assertFalse(doctor["healthy"])
        self.assertTrue(
            any(
                issue["type"] == "replacement-rollback-cleanup-pending"
                and issue["path"] == str(rollback)
                for issue in doctor["issues"]
            )
        )

    def test_content_comparison_treats_executable_bit_as_a_version_difference(self) -> None:
        target = self.create_skill("executable-skill")
        script = target / "scripts" / "run.sh"
        script.parent.mkdir()
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        script.chmod(0o644)
        source = self.root / "incoming" / "executable-skill"
        manager.shutil.copytree(target, source)
        (source / "scripts" / "run.sh").chmod(0o755)

        payload = self.capture_json(
            manager.cmd_adopt,
            Namespace(source=str(source), replace=False, apply=False),
        )

        self.assertEqual(payload["action"], "version-choice-required")
        self.assertEqual(payload["comparison"]["changed"], ["scripts/run.sh"])

    def test_adopt_rejects_incoming_source_nested_inside_canonical(self) -> None:
        target = self.create_skill("nested-source")
        source = target / "incoming"
        source.mkdir()
        (source / "SKILL.md").write_text(
            "---\nname: nested-source\ndescription: A nested incoming version.\n---\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(manager.ManagerError, "must not contain one another"):
            manager.cmd_adopt(
                Namespace(source=str(source), replace=True, apply=False), self.lib
            )

        self.assertTrue(target.is_dir())
        self.assertTrue(source.is_dir())

    def test_doctor_does_not_misclassify_an_ordinary_stage_name_as_rollback(self) -> None:
        self.create_skill("skills-manager")
        bootstrap = manager.GLOBAL_SKILLS_DIR / "skills-manager"
        bootstrap.parent.mkdir(parents=True)
        os.symlink(self.lib.skill_path("skills-manager"), bootstrap, target_is_directory=True)
        ordinary = self.lib.staging / "ordinary-rollback-shaped-name"
        ordinary.mkdir()

        payload = self.capture_json(manager.cmd_doctor, Namespace())

        self.assertTrue(payload["healthy"])
        self.assertFalse(
            any(issue["type"] == "replacement-rollback-cleanup-pending" for issue in payload["issues"])
        )

    def test_host_migrate_promotes_native_user_directory_to_canonical_symlink(self) -> None:
        source = self.home / ".codex" / "skills" / "migrated-skill"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "---\nname: migrated-skill\ndescription: A migrated Skill.\n---\n",
            encoding="utf-8",
        )

        payload = self.capture_json(
            manager.cmd_migrate,
            Namespace(
                source=str(source),
                host="codex",
                scope="global",
                project=None,
                workspace=None,
                state_dir=None,
                profile_home=None,
                apply=True,
            ),
        )

        target = self.lib.skill_path("migrated-skill")
        self.assertTrue(payload["apply"])
        self.assertTrue(target.is_dir())
        self.assertTrue(source.is_symlink())
        self.assertEqual(manager.resolved(source), manager.resolved(target))
        records = self.lib.load_state()["installations"]["migrated-skill"]
        self.assertEqual(records[0]["host"], "codex")

    def test_host_migrate_rolls_back_source_and_target_when_state_save_fails(self) -> None:
        source = self.home / ".codex" / "skills" / "migrate-rollback"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "---\nname: migrate-rollback\ndescription: A rollback Skill.\n---\n",
            encoding="utf-8",
        )
        self.lib.save_state = self.fail_state_save

        with self.assertRaisesRegex(OSError, "injected state write failure"):
            manager.cmd_migrate(
                Namespace(
                    source=str(source),
                    host="codex",
                    scope="global",
                    project=None,
                    workspace=None,
                    state_dir=None,
                    profile_home=None,
                    apply=True,
                ),
                self.lib,
            )

        self.assertTrue(source.is_dir())
        self.assertFalse(source.is_symlink())
        self.assertFalse(self.lib.skill_path("migrate-rollback").exists())
        self.assertEqual(list(self.lib.backups.iterdir()), [])

    def test_explicit_host_migration_clears_selected_legacy_state(self) -> None:
        source = manager.GLOBAL_SKILLS_DIR / "legacy-migrated"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "---\nname: legacy-migrated\ndescription: A legacy migrated Skill.\n---\n",
            encoding="utf-8",
        )
        state = self.lib.load_state()
        state["skill_scopes"]["legacy-migrated"] = "global"
        manager.record_exposure(
            state, source, source, "legacy-migrated", "global", None
        )
        self.lib.save_state(state)

        payload = self.capture_json(
            manager.cmd_migrate,
            Namespace(
                source=str(source),
                host="codex",
                scope="global",
                project=None,
                workspace=None,
                state_dir=None,
                profile_home=None,
                apply=True,
            ),
        )

        codex_link = self.home / ".codex" / "skills" / "legacy-migrated"
        updated = self.lib.load_state()
        self.assertTrue(payload["clears_legacy_binding"])
        self.assertFalse(source.exists())
        self.assertNotIn(str(source), updated["exposures"])
        self.assertNotIn("legacy-migrated", updated["skill_scopes"])
        self.assertTrue(codex_link.is_symlink())
        self.assertEqual(
            updated["installations"]["legacy-migrated"][0]["host"], "codex"
        )

    def test_host_native_migration_does_not_clear_unrelated_legacy_scope_marker(self) -> None:
        source = self.home / ".codex" / "skills" / "same-name"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "---\nname: same-name\ndescription: A host-native Skill.\n---\n",
            encoding="utf-8",
        )
        state = self.lib.load_state()
        state["skill_scopes"]["same-name"] = "project"
        self.lib.save_state(state)

        payload = self.capture_json(
            manager.cmd_migrate,
            Namespace(
                source=str(source),
                host="codex",
                scope="global",
                project=None,
                workspace=None,
                state_dir=None,
                profile_home=None,
                apply=True,
            ),
        )

        updated = self.lib.load_state()
        self.assertFalse(payload["clears_legacy_binding"])
        self.assertEqual(payload["legacy_scope_present"], "project")
        self.assertEqual(updated["skill_scopes"]["same-name"], "project")
        self.assertEqual(updated["installations"]["same-name"][0]["host"], "codex")

    def test_host_specific_destination_arguments_are_validated(self) -> None:
        project = self.root / "project"
        project.mkdir()
        with self.assertRaisesRegex(manager.ManagerError, "not valid for host"):
            manager.scope_link("demo", "agent", None, "codex", workspace=str(project))
        with self.assertRaisesRegex(manager.ManagerError, "--workspace"):
            manager.scope_link("demo", "global", None, "openclaw", workspace=str(project))
        with self.assertRaisesRegex(manager.ManagerError, "requires --workspace"):
            manager.scope_link("demo", "agent", None, "openclaw")
        with self.assertRaisesRegex(manager.ManagerError, "requires --project"):
            manager.scope_link("demo", "project", None, "hermes")

    def test_same_skill_can_have_independent_host_installations(self) -> None:
        target = self.create_skill("shared-canonical")
        common = {
            "skill": "shared-canonical",
            "scope": "global",
            "project": None,
            "workspace": None,
            "state_dir": None,
            "profile_home": None,
            "apply": True,
        }

        codex = self.capture_json(
            manager.cmd_expose, Namespace(host="codex", **common)
        )
        claude = self.capture_json(
            manager.cmd_expose, Namespace(host="claude-code", **common)
        )

        codex_link = self.home / ".codex" / "skills" / "shared-canonical"
        claude_link = self.home / ".claude" / "skills" / "shared-canonical"
        self.assertTrue(codex_link.is_symlink())
        self.assertTrue(claude_link.is_symlink())
        self.assertEqual(manager.resolved(codex_link), manager.resolved(target))
        self.assertEqual(manager.resolved(claude_link), manager.resolved(target))
        self.assertEqual(codex["host"], "codex")
        self.assertEqual(claude["host"], "claude-code")
        records = self.lib.load_state()["installations"]["shared-canonical"]
        self.assertEqual({item["host"] for item in records}, {"codex", "claude-code"})

    def test_openclaw_agent_and_hermes_project_report_runtime_requirements(self) -> None:
        self.create_skill("runtime-gated")
        workspace = self.root / "workspace"
        project = self.root / "project"
        workspace.mkdir()
        project.mkdir()

        openclaw = self.capture_json(
            manager.cmd_expose,
            Namespace(
                skill="runtime-gated",
                host="openclaw",
                scope="agent",
                project=None,
                workspace=str(workspace),
                state_dir=None,
                profile_home=None,
                apply=False,
            ),
        )
        hermes = self.capture_json(
            manager.cmd_expose,
            Namespace(
                skill="runtime-gated",
                host="hermes",
                scope="project",
                project=str(project),
                workspace=None,
                state_dir=None,
                profile_home=None,
                apply=False,
            ),
        )

        self.assertEqual(
            openclaw["links"][0]["requirements"][0]["type"],
            "openclaw-allow-symlink-target",
        )
        self.assertEqual(
            hermes["links"][0]["requirements"][0]["type"],
            "hermes-project-trust",
        )

    def test_hostless_unexpose_does_not_remove_codex_project_binding(self) -> None:
        target = self.create_skill("project-shared")
        project = self.root / "project"
        project.mkdir()
        args = Namespace(
            skill="project-shared",
            host="codex",
            scope="project",
            project=str(project),
            workspace=None,
            state_dir=None,
            profile_home=None,
            apply=True,
        )
        self.capture_json(manager.cmd_expose, args)
        link = project / ".agents" / "skills" / "project-shared"

        with self.assertRaisesRegex(manager.ManagerError, "belongs to host"):
            manager.cmd_unexpose(
                Namespace(
                    skill="project-shared",
                    scope="project",
                    project=str(project),
                    apply=True,
                ),
                self.lib,
            )

        self.assertTrue(link.is_symlink())
        self.assertEqual(manager.resolved(link), manager.resolved(target))

    def test_legacy_unexpose_clears_last_legacy_scope_marker(self) -> None:
        target = self.create_skill("legacy-cleanup")
        link = manager.GLOBAL_SKILLS_DIR / "legacy-cleanup"
        link.parent.mkdir(parents=True)
        os.symlink(target, link, target_is_directory=True)
        state = self.lib.load_state()
        state["skill_scopes"]["legacy-cleanup"] = "global"
        manager.record_exposure(
            state, link, target, "legacy-cleanup", "global", None
        )
        self.lib.save_state(state)

        payload = self.capture_json(
            manager.cmd_unexpose,
            Namespace(
                skill="legacy-cleanup",
                scope="global",
                project=None,
                apply=True,
            ),
        )

        self.assertTrue(payload["clear_legacy_scope"])
        self.assertFalse(link.exists())
        self.assertNotIn("legacy-cleanup", self.lib.load_state()["skill_scopes"])

    def test_host_expose_refuses_to_overwrite_legacy_record_at_same_project_link(self) -> None:
        target = self.create_skill("legacy-project")
        project = self.root / "project"
        project.mkdir()
        link = project / ".agents" / "skills" / "legacy-project"
        link.parent.mkdir(parents=True)
        os.symlink(target, link, target_is_directory=True)
        state = self.lib.load_state()
        state["skill_scopes"]["legacy-project"] = "project"
        manager.record_exposure(
            state, link, target, "legacy-project", "project", project
        )
        self.lib.save_state(state)

        with self.assertRaisesRegex(manager.ManagerError, "belongs to host 'legacy'"):
            manager.cmd_expose(
                Namespace(
                    skill="legacy-project",
                    host="codex",
                    scope="project",
                    project=str(project),
                    workspace=None,
                    state_dir=None,
                    profile_home=None,
                    apply=False,
                ),
                self.lib,
            )

        self.assertTrue(link.is_symlink())
        self.assertEqual(
            self.lib.load_state()["exposures"][str(link)]["host"], "legacy"
        )

    def test_legacy_unexpose_clears_scope_when_link_and_record_are_already_missing(self) -> None:
        self.create_skill("stale-legacy")
        state = self.lib.load_state()
        state["skill_scopes"]["stale-legacy"] = "global"
        self.lib.save_state(state)

        payload = self.capture_json(
            manager.cmd_unexpose,
            Namespace(
                skill="stale-legacy",
                scope="global",
                project=None,
                apply=True,
            ),
        )

        self.assertEqual(payload["result"], "missing-link")
        self.assertNotIn("stale-legacy", self.lib.load_state()["skill_scopes"])

    def test_codex_unexpose_refuses_unrecorded_legacy_project_link(self) -> None:
        target = self.create_skill("ambiguous-project")
        project = self.root / "project"
        project.mkdir()
        link = project / ".agents" / "skills" / "ambiguous-project"
        link.parent.mkdir(parents=True)
        os.symlink(target, link, target_is_directory=True)
        state = self.lib.load_state()
        state["skill_scopes"]["ambiguous-project"] = "project"
        self.lib.save_state(state)

        with self.assertRaisesRegex(manager.ManagerError, "Cannot prove Codex ownership"):
            manager.cmd_unexpose(
                Namespace(
                    skill="ambiguous-project",
                    host="codex",
                    scope="project",
                    project=str(project),
                    workspace=None,
                    state_dir=None,
                    profile_home=None,
                    apply=True,
                ),
                self.lib,
            )

        self.assertTrue(link.is_symlink())
        self.assertEqual(manager.resolved(link), manager.resolved(target))

    def test_doctor_accepts_valid_host_installation_and_is_read_only(self) -> None:
        self.create_skill("doctor-hosted")
        self.capture_json(
            manager.cmd_expose,
            Namespace(
                skill="doctor-hosted",
                host="codex",
                scope="global",
                project=None,
                workspace=None,
                state_dir=None,
                profile_home=None,
                apply=True,
            ),
        )
        before = self.lib.state_file.read_bytes()

        payload = self.capture_json(manager.cmd_doctor, Namespace())

        self.assertTrue(payload["healthy"], payload["issues"])
        self.assertEqual(payload["issues"], [])
        self.assertEqual(self.lib.state_file.read_bytes(), before)

    def test_doctor_accepts_reserved_manager_bootstrap_without_legacy_notice(self) -> None:
        target = self.create_skill("skills-manager")
        bootstrap = manager.GLOBAL_SKILLS_DIR / "skills-manager"
        bootstrap.parent.mkdir(parents=True)
        os.symlink(target, bootstrap, target_is_directory=True)
        state = self.lib.load_state()
        state["skill_scopes"]["skills-manager"] = "global"
        manager.record_exposure(
            state, bootstrap, target, "skills-manager", "global", None
        )
        self.lib.save_state(state)

        payload = self.capture_json(manager.cmd_doctor, Namespace())

        self.assertTrue(payload["healthy"], payload["issues"])
        self.assertFalse(
            any(
                notice["type"] == "legacy-binding-pending"
                and notice["path"] == str(bootstrap)
                for notice in payload["notices"]
            )
        )

    def test_doctor_requires_manager_bootstrap_but_not_claude_compatibility(self) -> None:
        self.create_skill("skills-manager")

        output = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(output):
            manager.cmd_doctor(Namespace(), self.lib)
        payload = json.loads(output.getvalue())

        self.assertIn("manager-bootstrap-not-ready", {item["type"] for item in payload["issues"]})
        self.assertFalse(
            any(".claude" in item["path"] for item in payload["issues"])
        )

    def test_host_aware_set_scope_records_canonical_only_installation(self) -> None:
        target = self.create_skill("future-project")

        payload = self.capture_json(
            manager.cmd_set_scope,
            Namespace(
                skills=["future-project"],
                host="hermes",
                scope="project",
                project=None,
                workspace=None,
                state_dir=None,
                profile_home=None,
                apply=True,
            ),
        )

        self.assertEqual(payload["host"], "hermes")
        records = self.lib.load_state()["installations"]["future-project"]
        self.assertEqual(records[0]["host"], "hermes")
        self.assertEqual(records[0]["scope"], "project")
        self.assertIsNone(records[0]["link"])
        self.assertTrue(target.is_dir())

        project = self.root / "future-root"
        project.mkdir()
        self.capture_json(
            manager.cmd_expose,
            Namespace(
                skill="future-project",
                host="hermes",
                scope="project",
                project=str(project),
                workspace=None,
                state_dir=None,
                profile_home=None,
                apply=True,
            ),
        )
        rebound = self.lib.load_state()["installations"]["future-project"]
        self.assertEqual(len(rebound), 1)
        self.assertEqual(
            rebound[0]["link"], str(project / ".hermes" / "skills" / "future-project")
        )

    def test_unset_scope_removes_only_canonical_only_installation(self) -> None:
        self.create_skill("unset-future")
        self.capture_json(
            manager.cmd_set_scope,
            Namespace(
                skills=["unset-future"],
                host="openclaw",
                scope="agent",
                project=None,
                workspace=None,
                state_dir=None,
                profile_home=None,
                apply=True,
            ),
        )

        with self.assertRaisesRegex(manager.ManagerError, "Remove host installations first"):
            manager.cmd_remove(Namespace(skill="unset-future", apply=False), self.lib)

        payload = self.capture_json(
            manager.cmd_unset_scope,
            Namespace(
                skills=["unset-future"],
                host="openclaw",
                scope="agent",
                apply=True,
            ),
        )

        self.assertTrue(payload["apply"])
        self.assertNotIn("unset-future", self.lib.load_state()["installations"])
        removal = self.capture_json(
            manager.cmd_remove, Namespace(skill="unset-future", apply=False)
        )
        self.assertEqual(removal["action"], "remove-canonical")

    def test_unset_scope_refuses_linked_installation(self) -> None:
        self.create_skill("unset-linked")
        self.capture_json(
            manager.cmd_expose,
            Namespace(
                skill="unset-linked",
                host="codex",
                scope="global",
                project=None,
                workspace=None,
                state_dir=None,
                profile_home=None,
                apply=True,
            ),
        )

        with self.assertRaisesRegex(manager.ManagerError, "Unexpose linked installations"):
            manager.cmd_unset_scope(
                Namespace(
                    skills=["unset-linked"],
                    host="codex",
                    scope="global",
                    apply=True,
                ),
                self.lib,
            )

    def test_skill_contract_uses_initialization_and_conflict_only_confirmation(self) -> None:
        skill_root = SCRIPT_PATH.parent.parent
        skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        guide_text = (skill_root / "references" / "user-guide.md").read_text(encoding="utf-8")
        combined_text = skill_text + guide_text

        self.assertNotIn("self-bootstrap", combined_text.lower())
        self.assertIn("Keep one canonical copy", skill_text)
        self.assertIn("--host codex|claude-code|openclaw|hermes", skill_text)
        self.assertIn("Keep functional-overlap checks enabled by default", skill_text)
        self.assertIn("the Agent makes the semantic decision", skill_text)
        self.assertIn("Never silently reinterpret version-1", skill_text)
        self.assertIn("do not disable discovery rules", skill_text)
        self.assertIn(
            "immediately repeat the command with `--apply`; do not pause for a second confirmation",
            skill_text,
        )
        self.assertIn(
            "immediately repeat it with `--apply` without displaying a confirmation prompt",
            guide_text,
        )
        self.assertIn("A choice already made explicitly", skill_text)
        self.assertIn("Ask only when preflight exposes a conflict", guide_text)
        for decision_support in (
            "read both `SKILL.md` files completely",
            "two to four bullets",
            "one recommendation with a reason",
            "one sentence naming every affected host/scope",
            "Do not lead with raw paths, fingerprints, or line-by-line diffs",
            "recommend **cancel** and offer a separate `skill-creator` merge",
            "accept one reply that maps choices to Skills",
        ):
            self.assertIn(decision_support, skill_text)
        self.assertIn("compact semantic comparison", guide_text)
        self.assertIn("difference is only formatting", guide_text)
        self.assertIn("Recommend **use existing** for non-behavioral-only differences", guide_text)
        self.assertIn("A bare statement such as “`SKILL.md` differs” is not sufficient", skill_text)
        version_example = guide_text.split("### Choose between different versions", 1)[1].split(
            "### Expose a group", 1
        )[0]
        for example_part in (
            "- Library-only:",
            "- Incoming-only:",
            "Recommendation:",
            "Impact:",
            "Choose use existing, use incoming, or cancel.",
        ):
            self.assertIn(example_part, version_example)
        self.assertLess(
            version_example.index("- Library-only:"),
            version_example.index("Recommendation:"),
        )
        self.assertLess(
            version_example.index("Recommendation:"), version_example.index("Impact:")
        )
        self.assertLess(
            version_example.index("Impact:"),
            version_example.index("Choose use existing, use incoming, or cancel."),
        )
        self.assertIn(
            "asking only when either remains unspecified or ambiguous", guide_text
        )
        self.assertIn(
            "asking only when it is missing, ambiguous, or materially changed by normalization",
            guide_text,
        )
        for decision_boundary in (
            "conflict or overwrite resolution",
            "version or overlap choice",
            "ambiguous host, scope, root, target, or batch selection",
            "missing authorization or runtime trust",
        ):
            self.assertIn(decision_boundary, skill_text)
            self.assertIn(decision_boundary, guide_text)
        for obsolete_rule in (
            "all other mutations require confirmation of the exact plan",
            "After confirmation, repeat with `--apply`",
            "Apply only after the user confirms that dry-run plan",
            "every mutation is a dry run until confirmed with `--apply`",
            "requires `--apply` after confirmation",
            "applied only after confirmation",
            "Ask which supported host and compatible scope should own it",
            "Ask for the required project or workspace root when applicable",
        ):
            self.assertNotIn(obsolete_rule, combined_text)
        for choice in ("keep both", "keep existing", "keep new", "cancel"):
            self.assertIn(choice, skill_text.lower())
        self.assertIn("reserved bootstrap exception", guide_text)
        self.assertIn("$HOME/.agents/skills/skills-manager", guide_text)
        self.assertIn("without a second Skills Manager registration", guide_text)

    def test_validator_ignores_extension_values_and_preserves_source(self) -> None:
        skill = self.create_skill("extended-skill")
        extensions = (
            'cli_version: ">=1.0.15"\n',
            "cli_version: null\n",
            "custom-flag: true\n",
            "custom-number: 42\n",
            "custom-empty:\n",
            "custom-list:\n  - first\n  - second\n",
            "custom-list:\n- first\n- second\n",
            "custom-map:\n  name: nested-name\n  description: nested description\n",
            "custom-notes: |+\n  name: not-a-top-level-key\n  More notes.\n",
            "custom-flow: [\n  first,\n  second]\n",
            "metadata: &extra\n  category: vendor\n",
            "compatibility: [custom, values]\n",
            "allowed-tools:\n  - vendor-command\n",
        )
        for extension in extensions:
            with self.subTest(extension=extension):
                content = (
                    "---\n"
                    "before: ignored\n"
                    "name: extended-skill\n"
                    f"{extension}"
                    "# The next required field is still read.\n"
                    "description: A valid description.\n"
                    "after: ignored\n"
                    "---\n\n# Skill body\n"
                ).encode("utf-8")
                (skill / "SKILL.md").write_bytes(content)

                result = manager.validate_skill(skill)

                self.assertTrue(result["valid"], result["errors"])
                self.assertEqual(result["name"], "extended-skill")
                self.assertEqual(result["description"], "A valid description.")
                self.assertEqual((skill / "SKILL.md").read_bytes(), content)

    def test_extensions_do_not_bypass_required_field_validation(self) -> None:
        skill = self.create_skill("required-fields")
        cases = (
            "description: A valid description.\n",
            "name: required-fields\n",
            "name: ''\ndescription: A valid description.\n",
            "name: 123\ndescription: A valid description.\n",
            "name: ../escape\ndescription: A valid description.\n",
            "name: another-directory\ndescription: A valid description.\n",
            "name: required-fields\ndescription: ''\n",
            "name: required-fields\ndescription: null\n",
            "name: required-fields\ndescription: [not, a, string]\n",
            "name: required-fields\ndescription:\n  nested: value\n",
        )
        for required in cases:
            with self.subTest(required=required):
                (skill / "SKILL.md").write_text(
                    "---\n"
                    "custom-map:\n"
                    "  name: required-fields\n"
                    "  description: Nested fields cannot replace top-level fields.\n"
                    f"{required}"
                    "cli_version: ignored\n"
                    "---\n",
                    encoding="utf-8",
                )
                result = manager.validate_skill(skill)
                self.assertFalse(result["valid"], required)

    def test_extension_support_keeps_basic_frontmatter_checks(self) -> None:
        skill = self.create_skill("bad-structure")
        cases = (
            b"name: bad-structure\ndescription: Missing delimiters.\n",
            b"---\nname: bad-structure\ndescription: No closing delimiter.\n",
            b"---\n  stray: indentation\nname: bad-structure\ndescription: Test.\n---\n",
            b"---\ncustom: ignored\nmissing colon\nname: bad-structure\ndescription: Test.\n---\n",
            b"---\nname: bad-structure\ndescription: Test.\ncustom: \xff\n---\n",
        )
        for content in cases:
            with self.subTest(content=content):
                (skill / "SKILL.md").write_bytes(content)
                self.assertFalse(manager.validate_skill(skill)["valid"])

    def test_migration_preserves_extension_fields_byte_for_byte(self) -> None:
        project = self.root / "project"
        source = project / ".agents" / "skills" / "vendor-skill"
        source.mkdir(parents=True)
        original = (
            b'---\r\nname: vendor-skill\r\ncli_version: ">=1.0.15"\r\n'
            b"vendor-data:\r\n  enabled: true\r\n  items: [one, two]\r\n"
            b"description: A vendor skill.\r\n---\r\n\r\n# Original body\r\n"
        )
        (source / "SKILL.md").write_bytes(original)
        state_before = self.lib.load_state()
        args = Namespace(
            source=str(source), host="codex", scope="project",
            project=str(project), apply=False,
        )

        dry_run = self.capture_json(manager.cmd_migrate, args)
        self.assertFalse(dry_run["apply"])
        self.assertFalse(source.is_symlink())
        self.assertFalse(self.lib.skill_path("vendor-skill").exists())
        self.assertEqual((source / "SKILL.md").read_bytes(), original)
        self.assertEqual(self.lib.load_state(), state_before)

        args.apply = True
        applied = self.capture_json(manager.cmd_migrate, args)
        target = self.lib.skill_path("vendor-skill")
        self.assertTrue(source.is_symlink())
        self.assertEqual(source.resolve(), target.resolve())
        for path in (source, target, Path(applied["backup"])):
            self.assertEqual((path / "SKILL.md").read_bytes(), original)
        self.assertTrue(manager.validate_skill(target)["valid"])

    def test_validator_accepts_block_description_and_metadata(self) -> None:
        skill = self.lib.skills / "block-description"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\n"
            "name: block-description\n"
            "description: >\n"
            "  A valid folded description for a test Skill.\n"
            "metadata:\n"
            "  short-description: Test metadata\n"
            "---\n",
            encoding="utf-8",
        )

        result = manager.validate_skill(skill)

        self.assertTrue(result["valid"], result["errors"])

    def test_remove_blocks_unrecorded_live_global_link(self) -> None:
        target = self.create_skill("linked-skill")
        state = self.lib.load_state()
        state["skill_scopes"]["linked-skill"] = "global"
        self.lib.save_state(state)
        link = manager.GLOBAL_SKILLS_DIR / "linked-skill"
        link.parent.mkdir(parents=True)
        os.symlink(target, link, target_is_directory=True)

        with self.assertRaisesRegex(manager.ManagerError, "active or recorded exposures"):
            manager.cmd_remove(Namespace(skill="linked-skill", apply=False), self.lib)

        self.assertTrue(target.is_dir())
        self.assertTrue(link.is_symlink())

    def test_remove_rolls_back_when_state_save_fails(self) -> None:
        target = self.create_skill("remove-rollback")
        state = self.lib.load_state()
        state["skill_scopes"]["remove-rollback"] = "project"
        self.lib.save_state(state)
        self.lib.save_state = self.fail_state_save

        with self.assertRaisesRegex(OSError, "injected state write failure"):
            manager.cmd_remove(Namespace(skill="remove-rollback", apply=True), self.lib)

        self.assertTrue(target.is_dir())
        self.assertEqual(list(self.lib.backups.iterdir()), [])

    def test_host_expose_rolls_back_link_when_state_save_fails(self) -> None:
        self.create_skill("expose-rollback")
        link = self.home / ".codex" / "skills" / "expose-rollback"
        self.lib.save_state = self.fail_state_save

        with self.assertRaisesRegex(OSError, "injected state write failure"):
            manager.cmd_expose(
                Namespace(
                    skill="expose-rollback",
                    host="codex",
                    scope="global",
                    project=None,
                    workspace=None,
                    state_dir=None,
                    profile_home=None,
                    apply=True,
                ),
                self.lib,
            )

        self.assertFalse(link.exists())
        self.assertFalse(link.is_symlink())

    def test_status_reports_each_host_installation_link_status(self) -> None:
        target = self.create_skill("status-links")
        correct = self.home / ".codex" / "skills" / "status-links"
        missing = self.home / ".claude" / "skills" / "status-links"
        correct.parent.mkdir(parents=True)
        os.symlink(target, correct, target_is_directory=True)
        state = self.lib.load_state()
        manager.record_installation(state, "status-links", "codex", "global", correct)
        manager.record_installation(state, "status-links", "claude-code", "global", missing)
        manager.record_installation(state, "status-links", "hermes", "project", None)
        self.lib.save_state(state)

        payload = self.capture_json(manager.cmd_status, Namespace())

        statuses = {
            (item["host"], item["scope"]): item["status"]
            for item in payload["installation_status"]["status-links"]
        }
        self.assertEqual(statuses[("codex", "global")], "already-correct")
        self.assertEqual(statuses[("claude-code", "global")], "create")
        self.assertEqual(statuses[("hermes", "project")], "canonical-only")

    def test_repair_repoints_only_a_symlink_and_records_installation(self) -> None:
        target = self.create_skill("repair-link")
        wrong = self.create_skill("wrong-target")
        link = self.home / ".codex" / "skills" / "repair-link"
        link.parent.mkdir(parents=True)
        os.symlink(wrong, link, target_is_directory=True)

        dry_run = self.capture_json(
            manager.cmd_repair,
            Namespace(
                skill="repair-link",
                host="codex",
                scope="global",
                project=None,
                workspace=None,
                state_dir=None,
                profile_home=None,
                apply=False,
            ),
        )

        self.assertEqual(dry_run["operation"], "repoint")
        self.assertTrue(dry_run["status"].startswith("conflict-symlink"))
        self.assertEqual(manager.resolved(link), manager.resolved(wrong))

        applied = self.capture_json(
            manager.cmd_repair,
            Namespace(
                skill="repair-link",
                host="codex",
                scope="global",
                project=None,
                workspace=None,
                state_dir=None,
                profile_home=None,
                apply=True,
            ),
        )

        self.assertEqual(applied["result"], "repaired")
        self.assertTrue(link.is_symlink())
        self.assertEqual(manager.resolved(link), manager.resolved(target))
        records = self.lib.load_state()["installations"]["repair-link"]
        self.assertEqual(records[0]["host"], "codex")

    def test_repair_refuses_real_directory_and_rolls_back_on_state_failure(self) -> None:
        target = self.create_skill("repair-safety")
        link = self.home / ".codex" / "skills" / "repair-safety"
        link.mkdir(parents=True)

        with self.assertRaisesRegex(manager.ManagerError, "non-symlink"):
            manager.cmd_repair(
                Namespace(
                    skill="repair-safety",
                    host="codex",
                    scope="global",
                    project=None,
                    workspace=None,
                    state_dir=None,
                    profile_home=None,
                    apply=True,
                ),
                self.lib,
            )
        self.assertTrue(link.is_dir())

        link.rmdir()
        wrong = self.create_skill("repair-old-target")
        os.symlink(wrong, link, target_is_directory=True)
        old_link_text = os.readlink(link)
        self.lib.save_state = self.fail_state_save
        with self.assertRaisesRegex(OSError, "injected state write failure"):
            manager.cmd_repair(
                Namespace(
                    skill="repair-safety",
                    host="codex",
                    scope="global",
                    project=None,
                    workspace=None,
                    state_dir=None,
                    profile_home=None,
                    apply=True,
                ),
                self.lib,
            )
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), old_link_text)
        self.assertEqual(manager.resolved(link), manager.resolved(wrong))

    def test_adopt_new_skill_rolls_back_canonical_when_state_save_fails(self) -> None:
        source = self.create_candidate("adopt-source", "adopt-rollback")
        self.lib.save_state = self.fail_state_save

        with self.assertRaisesRegex(OSError, "injected state write failure"):
            manager.cmd_adopt(
                Namespace(source=str(source), replace=False, apply=True), self.lib
            )

        self.assertTrue(source.is_dir())
        self.assertFalse(self.lib.skill_path("adopt-rollback").exists())
        self.assertEqual(list(self.lib.backups.iterdir()), [])

    def test_group_expose_preflights_all_members_before_mutation(self) -> None:
        first = self.create_skill("group-first")
        second = self.create_skill("group-second")
        group = self.lib.group_path("backend")
        manager.atomic_write(group, manager.group_text("backend", ["group-first", "group-second"]))
        conflict = self.home / ".codex" / "skills" / "group-second"
        conflict.mkdir(parents=True)

        with self.assertRaisesRegex(manager.ManagerError, "Exposure conflict"):
            manager.cmd_group_expose(
                Namespace(
                    group="backend",
                    host="codex",
                    scope="global",
                    project=None,
                    workspace=None,
                    state_dir=None,
                    profile_home=None,
                    apply=True,
                ),
                self.lib,
            )

        first_link = self.home / ".codex" / "skills" / "group-first"
        self.assertFalse(first_link.exists())
        self.assertTrue(first.is_dir())
        self.assertTrue(second.is_dir())

    def test_unexpose_rolls_back_when_state_save_fails(self) -> None:
        target = self.create_skill("unlink-rollback")
        link = manager.GLOBAL_SKILLS_DIR / "unlink-rollback"
        link.parent.mkdir(parents=True)
        os.symlink(target, link, target_is_directory=True)
        state = self.lib.load_state()
        state["skill_scopes"]["unlink-rollback"] = "global"
        manager.record_exposure(state, link, target, "unlink-rollback", "global", None)
        self.lib.save_state(state)
        self.lib.save_state = self.fail_state_save

        with self.assertRaisesRegex(OSError, "injected state write failure"):
            manager.cmd_unexpose(
                Namespace(skill="unlink-rollback", scope="global", project=None, apply=True),
                self.lib,
            )

        self.assertTrue(link.is_symlink())
        self.assertEqual(manager.resolved(link), manager.resolved(target))

    def test_group_delete_rolls_back_when_state_save_fails(self) -> None:
        group = self.lib.group_path("backend")
        manager.atomic_write(group, manager.group_text("backend", []))
        self.lib.save_state = self.fail_state_save

        with self.assertRaisesRegex(OSError, "injected state write failure"):
            manager.cmd_group_delete(Namespace(group="backend", apply=True), self.lib)

        self.assertTrue(group.is_file())
        self.assertEqual(list(self.lib.backups.iterdir()), [])

    def test_group_rename_rolls_back_when_source_unlink_fails(self) -> None:
        old_path = self.lib.group_path("backend")
        new_path = self.lib.group_path("services")
        manager.atomic_write(old_path, manager.group_text("backend", []))
        original_unlink = Path.unlink

        def fail_old_unlink(path: Path, *args: object, **kwargs: object) -> None:
            if path == old_path:
                raise OSError("injected unlink failure")
            original_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", new=fail_old_unlink):
            with self.assertRaisesRegex(OSError, "injected unlink failure"):
                manager.cmd_group_rename(
                    Namespace(group="backend", new_name="services", apply=True), self.lib
                )

        self.assertTrue(old_path.is_file())
        self.assertFalse(new_path.exists())


if __name__ == "__main__":
    unittest.main()
