import json
import pytest
from pathlib import Path
from codelens.extractor import extract_repo, extract_file, to_json

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestExtractRepo:
    def test_returns_repo_skeleton(self, tmp_path):
        (tmp_path / "main.py").write_text("def foo(): pass")
        result = extract_repo(tmp_path)
        assert result.total_files >= 1
        assert "python" in result.languages_found

    def test_skips_non_source_files(self, tmp_path):
        (tmp_path / "README.md").write_text("# docs")
        (tmp_path / "app.py").write_text("x = 1")
        result = extract_repo(tmp_path)
        paths = [f.path for f in result.files]
        assert not any(".md" in p for p in paths)

    def test_multi_language_repo(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1")
        src = tmp_path / "src"
        src.mkdir()
        (src / "index.ts").write_text("export const x = 1;")
        result = extract_repo(tmp_path)
        assert "python" in result.languages_found
        assert "typescript" in result.languages_found

    def test_posix_paths_in_skeleton(self, tmp_path):
        sub = tmp_path / "src" / "auth"
        sub.mkdir(parents=True)
        (sub / "login.py").write_text("def login(): pass")
        result = extract_repo(tmp_path)
        paths = [f.path for f in result.files]
        assert "src/auth/login.py" in paths

    def test_no_backslashes_in_paths(self, tmp_path):
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        (sub / "c.py").write_text("pass")
        result = extract_repo(tmp_path)
        for f in result.files:
            assert "\\" not in f.path

    def test_skips_node_modules(self, tmp_path):
        nm = tmp_path / "node_modules" / "lib"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("module.exports = {}")
        (tmp_path / "app.py").write_text("x = 1")
        result = extract_repo(tmp_path)
        paths = [f.path for f in result.files]
        assert not any("node_modules" in p for p in paths)

    def test_total_files_count(self, tmp_path):
        (tmp_path / "a.py").write_text("pass")
        (tmp_path / "b.py").write_text("pass")
        result = extract_repo(tmp_path)
        assert result.total_files >= 2

    def test_empty_repo(self, tmp_path):
        result = extract_repo(tmp_path)
        assert result.files == []
        assert result.total_files == 0

    def test_languages_found_sorted(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1")
        (tmp_path / "app.ts").write_text("const x = 1;")
        result = extract_repo(tmp_path)
        assert result.languages_found == sorted(result.languages_found)


class TestToJson:
    def test_valid_json_output(self, tmp_path):
        (tmp_path / "app.py").write_text("def foo(): pass")
        result = extract_repo(tmp_path)
        output = to_json(result)
        parsed = json.loads(output)
        assert "files" in parsed
        assert "total_files" in parsed
        assert "languages_found" in parsed

    def test_from_alias_in_json(self, tmp_path):
        (tmp_path / "app.py").write_text("import os\ndef foo(): pass")
        result = extract_repo(tmp_path)
        output = to_json(result)
        parsed = json.loads(output)
        file_data = parsed["files"][0]
        if file_data["imports"]:
            assert "from" in file_data["imports"][0]
            assert "from_" not in file_data["imports"][0]

    def test_indent_default(self, tmp_path):
        (tmp_path / "x.py").write_text("x = 1")
        result = extract_repo(tmp_path)
        output = to_json(result)
        assert "\n  " in output  # indented

    def test_repo_path_in_json(self, tmp_path):
        result = extract_repo(tmp_path)
        output = to_json(result)
        parsed = json.loads(output)
        assert "repo_path" in parsed


class TestExtractFile:
    def test_single_python_file(self, tmp_path):
        f = tmp_path / "foo.py"
        f.write_text("def bar(): pass")
        sk = extract_file(f, tmp_path)
        assert sk is not None
        assert sk.language == "python"

    def test_single_ts_file(self, tmp_path):
        f = tmp_path / "foo.ts"
        f.write_text("export const x = 1;")
        sk = extract_file(f, tmp_path)
        assert sk is not None
        assert sk.language == "typescript"

    def test_unsupported_file_returns_none(self, tmp_path):
        f = tmp_path / "notes.md"
        f.write_text("# hello")
        assert extract_file(f, tmp_path) is None

    def test_path_is_posix(self, tmp_path):
        sub = tmp_path / "src"
        sub.mkdir()
        f = sub / "app.py"
        f.write_text("x = 1")
        sk = extract_file(f, tmp_path)
        assert sk is not None
        assert "\\" not in sk.path
        assert sk.path == "src/app.py"

    def test_fixture_python(self):
        fixture = FIXTURES_DIR / "sample_python.py"
        sk = extract_file(fixture, FIXTURES_DIR)
        assert sk is not None
        assert sk.language == "python"
        assert len(sk.symbols) > 0

    def test_fixture_typescript(self):
        fixture = FIXTURES_DIR / "sample_typescript.ts"
        sk = extract_file(fixture, FIXTURES_DIR)
        assert sk is not None
        assert sk.language == "typescript"
        assert len(sk.exports) > 0
