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
SPEC = importlib.util.spec_from_file_location("skills_manager_under_test", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load {SCRIPT_PATH}")
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


class SkillsManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="skills-manager-test-")
        self.root = Path(self.temp_dir.name)
        self.original_global_skills_dir = manager.GLOBAL_SKILLS_DIR
        manager.GLOBAL_SKILLS_DIR = self.root / "home" / ".agents" / "skills"
        self.lib = manager.Library(self.root / "library")
        self.lib.ensure_layout()

    def tearDown(self) -> None:
        manager.GLOBAL_SKILLS_DIR = self.original_global_skills_dir
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

    def test_help_shows_only_initialization_terminology(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            manager.build_parser().print_help()

        help_text = output.getvalue()
        self.assertIn("initialize", help_text)
        self.assertNotIn("bootstrap", help_text)

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
        self.assertFalse(dry_run["apply"])
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

    def test_skill_contract_uses_initialization_and_conflict_only_confirmation(self) -> None:
        skill_root = SCRIPT_PATH.parent.parent
        skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        guide_text = (skill_root / "references" / "user-guide.md").read_text(encoding="utf-8")

        self.assertNotIn("self-bootstrap", (skill_text + guide_text).lower())
        self.assertIn("Immediately rerun each conflict-free migration", skill_text)
        self.assertIn("Present only the conflicting items for confirmation", skill_text)
        self.assertIn("Keep functional-overlap checks enabled by default", skill_text)
        self.assertIn("the Agent makes the semantic decision", skill_text)
        self.assertIn("before any `adopt`, canonical promotion, or exposure", skill_text)
        self.assertIn("Stage or identify every candidate", skill_text)
        self.assertIn("before mutation", skill_text)
        for choice in ("Keep both", "Keep existing", "Keep new", "Cancel"):
            self.assertIn(choice, skill_text)
        self.assertIn("project scope was selected without an existing root", skill_text)
        self.assertIn("Never merge, delete, or replace", guide_text)

    def test_validator_rejects_unexpected_frontmatter_key(self) -> None:
        skill = self.lib.skills / "unexpected-key"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\n"
            "name: unexpected-key\n"
            "description: A valid description.\n"
            "bogus-field: rejected\n"
            "---\n",
            encoding="utf-8",
        )

        result = manager.validate_skill(skill)

        self.assertFalse(result["valid"])
        self.assertTrue(any("Unexpected frontmatter key" in error for error in result["errors"]))

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
