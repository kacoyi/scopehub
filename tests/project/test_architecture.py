"""Prevent dependency aliases from hiding forbidden layer relationships."""

from pathlib import Path
import tempfile
import unittest

from scripts.check_architecture import rust_boundaries


class ArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'Cargo.toml').write_text('[workspace]\n')
        (self.root / 'crates/domain').mkdir(parents=True)

    def domain(self, dependencies):
        (self.root / 'crates/domain/Cargo.toml').write_text(
            '[package]\nname = "scopehub-domain"\nversion = "0.1.0"\n' + dependencies)

    def test_workspace_alias_cannot_hide_database_dependency(self):
        (self.root / 'Cargo.toml').write_text('[workspace.dependencies]\nstore = { package = "rusqlite", version = "1" }\n')
        self.domain('[dependencies]\nstore = { workspace = true }\n')
        self.assertIn('Infrastructure dependency in scopehub-domain: rusqlite', rust_boundaries(self.root))

    def test_target_specific_workspace_alias_cannot_hide_internal_layer(self):
        (self.root / 'Cargo.toml').write_text('[workspace.dependencies]\nport = { package = "scopehub-persistence", path = "crates/persistence" }\n')
        (self.root / 'crates/persistence').mkdir()
        (self.root / 'crates/persistence/Cargo.toml').write_text('[package]\nname = "scopehub-persistence"\n')
        self.domain('[target.\'cfg(windows)\'.dependencies]\nport = { workspace = true }\n')
        self.assertIn('Forbidden internal dependency: scopehub-domain -> scopehub-persistence', rust_boundaries(self.root))

    def test_normal_workspace_library_is_allowed(self):
        (self.root / 'Cargo.toml').write_text('[workspace.dependencies]\nserde = "1"\n')
        self.domain('[dependencies]\nserde = { workspace = true }\n')
        self.assertEqual([], rust_boundaries(self.root))
