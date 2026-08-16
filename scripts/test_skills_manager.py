#!/usr/bin/env python3
"""Isolated regression tests for skills_manager.py."""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from argparse import Namespace
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

    def fail_state_save(self, state: dict[str, object]) -> None:
        raise OSError("injected state write failure")

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
