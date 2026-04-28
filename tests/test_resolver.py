"""Tests for codelens.resolver — import path resolution."""
from __future__ import annotations
import os
import pytest
from pathlib import Path
from codelens.resolver import resolve


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Create a minimal fake repo layout."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "utils.py").write_text("# utils")
    (tmp_path / "src" / "models.py").write_text("# models")
    (tmp_path / "src" / "core").mkdir()
    (tmp_path / "src" / "core" / "__init__.py").write_text("# core init")
    (tmp_path / "src" / "core" / "engine.py").write_text("# engine")
    (tmp_path / "components").mkdir()
    (tmp_path / "components" / "Button.tsx").write_text("// button")
    (tmp_path / "components" / "index.ts").write_text("// index")
    return tmp_path


class TestExternalPackages:
    def test_stdlib_returns_none(self, repo):
        assert resolve("os", "src/main.py", repo) is None

    def test_third_party_returns_none(self, repo):
        assert resolve("numpy", "src/main.py", repo) is None

    def test_scoped_package_returns_none(self, repo):
        assert resolve("@org/package", "src/main.py", repo) is None


class TestRelativeResolution:
    def test_sibling_py_file(self, repo):
        result = resolve("./utils", "src/models.py", repo)
        assert result == "src/utils.py"

    def test_sibling_without_dot_slash_returns_none(self, repo):
        # "utils" without "./" is treated as external
        assert resolve("utils", "src/models.py", repo) is None

    def test_parent_dir_relative(self, repo):
        result = resolve("../models", "src/core/engine.py", repo)
        assert result == "src/models.py"

    def test_tsx_extension(self, repo):
        result = resolve("./Button", "components/index.ts", repo)
        assert result == "components/Button.tsx"

    def test_directory_with_index_ts(self, repo):
        result = resolve("./components", "main.ts", repo)
        assert result == "components/index.ts"

    def test_directory_with_init_py(self, repo):
        result = resolve("./src/core", "main.py", repo)
        assert result == "src/core/__init__.py"

    def test_file_not_in_repo_returns_none(self, repo):
        assert resolve("./nonexistent", "src/main.py", repo) is None

    def test_already_has_extension(self, repo):
        result = resolve("./utils.py", "src/models.py", repo)
        assert result == "src/utils.py"


class TestEdgeCases:
    def test_empty_import_returns_none(self, repo):
        assert resolve("", "src/main.py", repo) is None

    def test_dotdot_past_root_returns_none(self, repo):
        # Going above repo root should not resolve
        assert resolve("../../outside", "src/main.py", repo) is None
