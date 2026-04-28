import pytest
from pathlib import Path
from codelens.parsers.typescript_parser import TypeScriptParser, JavaScriptParser

TS_FIXTURE = Path(__file__).parent / "fixtures" / "sample_typescript.ts"
JS_FIXTURE = Path(__file__).parent / "fixtures" / "sample_javascript.js"


@pytest.fixture
def ts_parser():
    return TypeScriptParser()


@pytest.fixture
def js_parser():
    return JavaScriptParser()


@pytest.fixture
def ts_skeleton(ts_parser):
    return ts_parser.parse(TS_FIXTURE.read_bytes(), "fixtures/sample_typescript.ts")


@pytest.fixture
def js_skeleton(js_parser):
    return js_parser.parse(JS_FIXTURE.read_bytes(), "fixtures/sample_javascript.js")


class TestTypeScriptParserBasics:
    def test_language(self, ts_skeleton):
        assert ts_skeleton.language == "typescript"

    def test_path(self, ts_skeleton):
        assert ts_skeleton.path == "fixtures/sample_typescript.ts"

    def test_loc_positive(self, ts_skeleton):
        assert ts_skeleton.loc > 0

    def test_supports_ts(self, ts_parser):
        assert ts_parser.supports(Path("foo.ts"))
        assert ts_parser.supports(Path("foo.tsx"))
        assert not ts_parser.supports(Path("foo.js"))


class TestTypeScriptParserImports:
    def test_named_import(self, ts_skeleton):
        entry = next(
            i for i in ts_skeleton.imports
            if i.from_ == "./db"
        )
        assert "UserModel" in entry.symbols

    def test_default_import(self, ts_skeleton):
        froms = [i.from_ for i in ts_skeleton.imports]
        assert "bcrypt" in froms

    def test_type_import(self, ts_skeleton):
        froms = [i.from_ for i in ts_skeleton.imports]
        assert "./types" in froms


class TestTypeScriptParserExports:
    def test_exports_include_function(self, ts_skeleton):
        assert "loginUser" in ts_skeleton.exports

    def test_exports_include_class(self, ts_skeleton):
        assert "AuthService" in ts_skeleton.exports

    def test_exports_include_const(self, ts_skeleton):
        assert "TIMEOUT_MS" in ts_skeleton.exports

    def test_exports_include_type(self, ts_skeleton):
        assert "UserId" in ts_skeleton.exports

    def test_re_export_star(self, ts_skeleton):
        assert "*" in ts_skeleton.exports


class TestTypeScriptParserSymbols:
    def test_async_function_symbol(self, ts_skeleton):
        fn = next(s for s in ts_skeleton.symbols if s.name == "loginUser")
        assert fn.kind == "function"
        assert fn.is_async is True
        assert fn.is_exported is True

    def test_function_has_jsdoc(self, ts_skeleton):
        fn = next(s for s in ts_skeleton.symbols if s.name == "loginUser")
        assert fn.doc is not None
        assert "Authenticates" in fn.doc

    def test_interface_symbol(self, ts_skeleton):
        iface = next(s for s in ts_skeleton.symbols if s.name == "LoginOptions")
        assert iface.kind == "type"
        assert iface.is_exported is True

    def test_class_symbol(self, ts_skeleton):
        cls = next(s for s in ts_skeleton.symbols if s.name == "AuthService")
        assert cls.kind == "class"
        assert cls.is_exported is True

    def test_class_has_methods(self, ts_skeleton):
        method_names = [s.name for s in ts_skeleton.symbols if s.kind == "method"]
        assert "validateSession" in method_names

    def test_const_symbol(self, ts_skeleton):
        const = next((s for s in ts_skeleton.symbols if s.name == "TIMEOUT_MS"), None)
        assert const is not None
        assert const.kind == "variable"
        assert const.is_exported is True

    def test_type_alias_symbol(self, ts_skeleton):
        t = next((s for s in ts_skeleton.symbols if s.name == "UserId"), None)
        assert t is not None
        assert t.kind == "type"

    def test_entrypoint_not_detected(self, ts_skeleton):
        assert ts_skeleton.is_entrypoint is False


class TestTypeScriptParserEdgeCases:
    def test_empty_file(self, ts_parser):
        sk = ts_parser.parse(b"", "empty.ts")
        assert sk.language == "typescript"
        assert sk.imports == []

    def test_bom_stripped(self, ts_parser):
        source = b"\xef\xbb\xbfexport const x = 1;\n"
        sk = ts_parser.parse(source, "bom.ts")
        assert sk.language == "typescript"

    def test_entrypoint_index(self, ts_parser):
        sk = ts_parser.parse(b"export const x = 1;", "index.ts")
        assert sk.is_entrypoint is True

    def test_malformed_source_no_crash(self, ts_parser):
        sk = ts_parser.parse(b"export function {{ broken", "broken.ts")
        assert sk.language == "typescript"


class TestJavaScriptParser:
    def test_language(self, js_skeleton):
        assert js_skeleton.language == "javascript"

    def test_supports_js(self, js_parser):
        assert js_parser.supports(Path("foo.js"))
        assert js_parser.supports(Path("foo.jsx"))
        assert js_parser.supports(Path("foo.mjs"))
        assert not js_parser.supports(Path("foo.ts"))

    def test_export_function(self, js_skeleton):
        assert "createServer" in js_skeleton.exports

    def test_export_const(self, js_skeleton):
        assert "DEFAULT_PORT" in js_skeleton.exports

    def test_function_symbol(self, js_skeleton):
        fn = next((s for s in js_skeleton.symbols if s.name == "createServer"), None)
        assert fn is not None
        assert fn.kind == "function"
