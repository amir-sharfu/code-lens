import pytest
from codelens.models import ImportEntry, SymbolEntry, FileSkeleton, RepoSkeleton


class TestImportEntry:
    def test_from_alias_serializes_correctly(self):
        entry = ImportEntry(**{"from": "./db", "symbols": ["UserModel"]})
        d = entry.model_dump(by_alias=True)
        assert d["from"] == "./db"
        assert "from_" not in d

    def test_from_field_accessible_as_from_(self):
        entry = ImportEntry(**{"from": "os"})
        assert entry.from_ == "os"

    def test_default_symbols_is_empty_list(self):
        entry = ImportEntry(**{"from": "os"})
        assert entry.symbols == []

    def test_is_dynamic_defaults_false(self):
        entry = ImportEntry(**{"from": "os"})
        assert entry.is_dynamic is False

    def test_populate_by_name_allows_from_(self):
        entry = ImportEntry(from_="os")
        assert entry.from_ == "os"


class TestSymbolEntry:
    def test_required_fields(self):
        sym = SymbolEntry(kind="function", name="foo", signature="def foo()", line=1)
        assert sym.name == "foo"
        assert sym.doc is None
        assert sym.is_async is False
        assert sym.is_exported is False

    def test_kind_validation(self):
        for kind in ("function", "class", "method", "variable", "type"):
            sym = SymbolEntry(kind=kind, name="x", signature="x", line=1)
            assert sym.kind == kind


class TestFileSkeleton:
    def test_to_dict_uses_aliases(self):
        entry = ImportEntry(**{"from": "os", "symbols": []})
        fs = FileSkeleton(path="foo.py", language="python", imports=[entry])
        d = fs.to_dict()
        assert d["imports"][0]["from"] == "os"
        assert "from_" not in d["imports"][0]

    def test_defaults(self):
        fs = FileSkeleton(path="foo.py", language="python")
        assert fs.imports == []
        assert fs.exports == []
        assert fs.symbols == []
        assert fs.loc == 0
        assert fs.is_entrypoint is False
        assert fs.is_auto_generated is False


class TestRepoSkeleton:
    def test_get_file_returns_none_for_missing(self):
        repo = RepoSkeleton(repo_path="/tmp")
        assert repo.get_file("nonexistent.py") is None

    def test_get_file_returns_correct_skeleton(self):
        fs = FileSkeleton(path="src/app.py", language="python")
        repo = RepoSkeleton(repo_path="/tmp", files=[fs])
        assert repo.get_file("src/app.py") is fs

    def test_defaults(self):
        repo = RepoSkeleton(repo_path="/tmp")
        assert repo.files == []
        assert repo.total_files == 0
        assert repo.skipped_files == 0
        assert repo.languages_found == []
