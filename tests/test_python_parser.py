import pytest
from pathlib import Path
from codelens.parsers.python_parser import PythonParser

FIXTURE = Path(__file__).parent / "fixtures" / "sample_python.py"


@pytest.fixture
def parser():
    return PythonParser()


@pytest.fixture
def skeleton(parser):
    return parser.parse(FIXTURE.read_bytes(), "fixtures/sample_python.py")


class TestPythonParserBasics:
    def test_language(self, skeleton):
        assert skeleton.language == "python"

    def test_path(self, skeleton):
        assert skeleton.path == "fixtures/sample_python.py"

    def test_loc_positive(self, skeleton):
        assert skeleton.loc > 0

    def test_supports_py(self, parser):
        assert parser.supports(Path("foo.py"))
        assert parser.supports(Path("foo.pyi"))
        assert not parser.supports(Path("foo.ts"))


class TestPythonParserImports:
    def test_stdlib_imports(self, skeleton):
        froms = [i.from_ for i in skeleton.imports]
        assert "os" in froms
        assert "sys" in froms

    def test_pathlib_import(self, skeleton):
        entry = next(i for i in skeleton.imports if i.from_ == "pathlib")
        assert "Path" in entry.symbols

    def test_relative_import_single_dot(self, skeleton):
        froms = [i.from_ for i in skeleton.imports]
        assert ".utils" in froms

    def test_relative_import_symbols(self, skeleton):
        entry = next(i for i in skeleton.imports if i.from_ == ".utils")
        assert "helper_func" in entry.symbols

    def test_relative_import_double_dot(self, skeleton):
        froms = [i.from_ for i in skeleton.imports]
        assert "..config" in froms


class TestPythonParserExports:
    def test_exports_from_all(self, skeleton):
        assert "PublicClass" in skeleton.exports
        assert "public_function" in skeleton.exports

    def test_private_not_in_exports(self, skeleton):
        assert "_private_function" not in skeleton.exports

    def test_exports_is_list(self, skeleton):
        assert isinstance(skeleton.exports, list)


class TestPythonParserSymbols:
    def test_class_symbol(self, skeleton):
        cls = next(s for s in skeleton.symbols if s.name == "PublicClass")
        assert cls.kind == "class"
        assert "PublicClass" in cls.signature
        assert cls.doc is not None
        assert "public class" in cls.doc.lower()
        assert cls.line > 0

    def test_class_is_exported(self, skeleton):
        cls = next(s for s in skeleton.symbols if s.name == "PublicClass")
        assert cls.is_exported is True

    def test_async_function_symbol(self, skeleton):
        fn = next(s for s in skeleton.symbols if s.name == "public_function")
        assert fn.kind == "function"
        assert fn.is_async is True
        assert fn.doc is not None
        assert fn.is_exported is True

    def test_async_signature(self, skeleton):
        fn = next(s for s in skeleton.symbols if s.name == "public_function")
        assert "async def" in fn.signature
        assert "email" in fn.signature
        assert "password" in fn.signature

    def test_private_function_not_exported(self, skeleton):
        fn = next(s for s in skeleton.symbols if s.name == "_private_function")
        assert fn.is_exported is False

    def test_method_extraction(self, skeleton):
        method = next((s for s in skeleton.symbols if s.name == "method"), None)
        assert method is not None
        assert method.kind == "method"

    def test_private_method_extraction(self, skeleton):
        method = next((s for s in skeleton.symbols if s.name == "_private_method"), None)
        assert method is not None
        assert method.is_exported is False

    def test_constant_extraction(self, skeleton):
        names = [s.name for s in skeleton.symbols]
        assert "CONSTANT" in names

    def test_entrypoint_detected(self, skeleton):
        assert skeleton.is_entrypoint is True


class TestPythonParserEdgeCases:
    def test_syntax_error_returns_partial_skeleton(self, parser):
        bad_source = b"def foo(:\n    pass\n"
        sk = parser.parse(bad_source, "bad.py")
        assert sk.language == "python"
        assert sk.loc > 0
        assert sk.imports == []
        assert sk.symbols == []

    def test_empty_file(self, parser):
        sk = parser.parse(b"", "empty.py")
        assert sk.language == "python"
        assert sk.imports == []

    def test_bom_stripped(self, parser):
        source = b"\xef\xbb\xbf# UTF-8 BOM\nx = 1\n"
        sk = parser.parse(source, "bom.py")
        assert sk.language == "python"

    def test_entrypoint_by_filename(self, parser):
        sk = parser.parse(b"x = 1", "main.py")
        assert sk.is_entrypoint is True

    def test_non_entrypoint_filename(self, parser):
        sk = parser.parse(b"x = 1", "utils.py")
        assert sk.is_entrypoint is False

    def test_relative_import_star_becomes_empty_symbols(self, parser):
        source = b"from . import *\n"
        sk = parser.parse(source, "foo.py")
        entry = next(i for i in sk.imports if i.from_ == ".")
        assert entry.symbols == []
