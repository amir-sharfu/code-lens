from __future__ import annotations
from pathlib import Path
import tree_sitter_typescript as tsts
import tree_sitter_javascript as tsjs
from tree_sitter import Language, Parser
from codelens.parsers.base import BaseParser
from codelens.models import FileSkeleton, ImportEntry, SymbolEntry

_IMPORT_STATEMENT = "import_statement"
_EXPORT_STATEMENT = "export_statement"
_FUNCTION_DECLARATION = "function_declaration"
_ARROW_FUNCTION = "arrow_function"
_CLASS_DECLARATION = "class_declaration"
_METHOD_DEFINITION = "method_definition"
_LEXICAL_DECLARATION = "lexical_declaration"
_VARIABLE_DECLARATION = "variable_declaration"
_EXPORT_CLAUSE = "export_clause"
_NAMESPACE_IMPORT = "namespace_import"
_NAMED_IMPORTS = "named_imports"
_IMPORT_SPECIFIER = "import_specifier"
_TYPE_ALIAS_DECLARATION = "type_alias_declaration"
_INTERFACE_DECLARATION = "interface_declaration"

_DECL_TYPES = frozenset({
    _FUNCTION_DECLARATION, _CLASS_DECLARATION, _LEXICAL_DECLARATION,
    _VARIABLE_DECLARATION, _TYPE_ALIAS_DECLARATION, _INTERFACE_DECLARATION,
})


class TypeScriptParser(BaseParser):
    LANGUAGE = "typescript"
    EXTENSIONS = frozenset({".ts", ".tsx"})

    def __init__(self) -> None:
        self._lang = Language(tsts.language_typescript())
        self._parser = Parser(self._lang)

    def parse(self, source: bytes, rel_path: str) -> FileSkeleton:
        source = self._strip_bom(source)
        loc = self._count_lines(source)
        skeleton = FileSkeleton(path=rel_path, language=self.LANGUAGE, loc=loc)

        try:
            tree = self._parser.parse(source)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("tree-sitter parse failed for %s: %s", rel_path, exc)
            return skeleton

        root = tree.root_node
        skeleton.imports = self._extract_imports(root, source)
        export_names, export_set = self._extract_exports(root, source)
        skeleton.exports = export_names
        skeleton.symbols = self._extract_symbols(root, source, export_set)
        skeleton.is_entrypoint = self._detect_entrypoint(rel_path)
        return skeleton

    def _extract_imports(self, root, source: bytes) -> list[ImportEntry]:
        entries: list[ImportEntry] = []
        for node in self._iter_children(root, _IMPORT_STATEMENT):
            from_path = self._get_string_field(node, source, "source")
            if from_path is None:
                continue
            symbols = self._get_import_symbols(node, source)
            entries.append(ImportEntry(**{"from": from_path, "symbols": symbols}))
        return entries

    def _get_import_symbols(self, node, source: bytes) -> list[str]:
        symbols: list[str] = []
        # named_imports may be a direct child or nested inside import_clause
        for child in node.children:
            if child.type == _NAMED_IMPORTS:
                symbols.extend(self._parse_named_imports(child, source))
            elif child.type == "import_clause":
                for inner in child.children:
                    if inner.type == _NAMED_IMPORTS:
                        symbols.extend(self._parse_named_imports(inner, source))
                    elif inner.type == _NAMESPACE_IMPORT:
                        symbols.append("*")
            elif child.type == _NAMESPACE_IMPORT:
                symbols.append("*")
        return symbols

    def _parse_named_imports(self, node, source: bytes) -> list[str]:
        result: list[str] = []
        for spec in node.children:
            if spec.type == _IMPORT_SPECIFIER:
                name_node = spec.child_by_field_name("name") or (spec.children[0] if spec.children else None)
                if name_node:
                    result.append(self._text(name_node, source))
        return result

    def _extract_exports(self, root, source: bytes) -> tuple[list[str], set[str]]:
        export_names: list[str] = []
        export_set: set[str] = set()

        for node in self._iter_children(root, _EXPORT_STATEMENT):
            node_text = self._node_text(node, source)

            # export * from './foo'
            if "export *" in node_text and "from" in node_text:
                export_names.append("*")
                continue

            # export { foo, bar }
            for clause in node.children:
                if clause.type == _EXPORT_CLAUSE:
                    for spec in clause.children:
                        if spec.type == "export_specifier":
                            # first identifier child is the local name
                            for c in spec.children:
                                if c.type in ("identifier", "property_identifier"):
                                    name = self._text(c, source)
                                    export_names.append(name)
                                    export_set.add(name)
                                    break

            # export function/class/const/type/interface
            decl = self._find_child(node, _DECL_TYPES)
            if decl:
                name = self._get_decl_name(decl, source)
                if name:
                    export_names.append(name)
                    export_set.add(name)

        return export_names, export_set

    def _extract_symbols(self, root, source: bytes, export_set: set[str]) -> list[SymbolEntry]:
        symbols: list[SymbolEntry] = []

        for node in root.children:
            actual = node
            if node.type == _EXPORT_STATEMENT:
                inner = self._find_child(node, _DECL_TYPES)
                if inner:
                    actual = inner

            if actual.type == _FUNCTION_DECLARATION:
                sym = self._function_symbol(actual, source, export_set)
                if sym:
                    symbols.append(sym)

            elif actual.type == _CLASS_DECLARATION:
                sym = self._class_symbol(actual, source, export_set)
                if sym:
                    symbols.append(sym)
                # Methods are inside class_body, not direct children of class_declaration
                body = actual.child_by_field_name("body")
                if body:
                    for method in self._iter_children(body, _METHOD_DEFINITION):
                        m = self._method_symbol(method, source)
                        if m:
                            symbols.append(m)

            elif actual.type in (_LEXICAL_DECLARATION, _VARIABLE_DECLARATION):
                symbols.extend(self._variable_symbols(actual, source, export_set))

            elif actual.type == _INTERFACE_DECLARATION:
                name = self._get_decl_name(actual, source)
                if name:
                    symbols.append(SymbolEntry(
                        kind="type",
                        name=name,
                        signature=f"interface {name}",
                        line=actual.start_point[0] + 1,
                        is_exported=name in export_set,
                    ))

            elif actual.type == _TYPE_ALIAS_DECLARATION:
                name = self._get_decl_name(actual, source)
                if name:
                    symbols.append(SymbolEntry(
                        kind="type",
                        name=name,
                        signature=f"type {name}",
                        line=actual.start_point[0] + 1,
                        is_exported=name in export_set,
                    ))

        return symbols

    def _function_symbol(self, node, source: bytes, export_set: set[str]) -> SymbolEntry | None:
        name = self._get_decl_name(node, source)
        if not name:
            return None
        sig = self._node_text(node, source).split("{")[0].strip().rstrip()
        doc = self._extract_jsdoc(node, source)
        return SymbolEntry(
            kind="function",
            name=name,
            signature=sig,
            doc=doc,
            line=node.start_point[0] + 1,
            is_async="async" in sig,
            is_exported=name in export_set,
        )

    def _class_symbol(self, node, source: bytes, export_set: set[str]) -> SymbolEntry | None:
        name = self._get_decl_name(node, source)
        if not name:
            return None
        sig = self._node_text(node, source).split("{")[0].strip()
        doc = self._extract_jsdoc(node, source)
        return SymbolEntry(
            kind="class",
            name=name,
            signature=sig,
            doc=doc,
            line=node.start_point[0] + 1,
            is_exported=name in export_set,
        )

    def _method_symbol(self, node, source: bytes) -> SymbolEntry | None:
        name_node = self._find_child(node, frozenset({"property_identifier", "identifier"}))
        if not name_node:
            return None
        name = self._text(name_node, source)
        sig = self._node_text(node, source).split("{")[0].strip()
        return SymbolEntry(
            kind="method",
            name=name,
            signature=sig,
            line=node.start_point[0] + 1,
        )

    def _variable_symbols(self, node, source: bytes, export_set: set[str]) -> list[SymbolEntry]:
        symbols: list[SymbolEntry] = []
        for declarator in self._iter_children(node, "variable_declarator"):
            name_node = declarator.child_by_field_name("name")
            if not name_node:
                continue
            name = self._text(name_node, source)
            value = declarator.child_by_field_name("value")
            if value and value.type in (_ARROW_FUNCTION, "function_expression", "function"):
                sig = self._node_text(node, source).split("{")[0].strip()
                symbols.append(SymbolEntry(
                    kind="function",
                    name=name,
                    signature=sig,
                    line=node.start_point[0] + 1,
                    is_exported=name in export_set,
                ))
            else:
                symbols.append(SymbolEntry(
                    kind="variable",
                    name=name,
                    signature=f"const {name}",
                    line=node.start_point[0] + 1,
                    is_exported=name in export_set,
                ))
        return symbols

    def _node_text(self, node, source: bytes) -> str:
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _text(self, node, source: bytes) -> str:
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _get_decl_name(self, node, source: bytes) -> str | None:
        name_node = node.child_by_field_name("name")
        if name_node:
            return self._text(name_node, source)
        # lexical/variable declarations: name is inside variable_declarator
        if node.type in (_LEXICAL_DECLARATION, _VARIABLE_DECLARATION):
            for child in node.children:
                if child.type == "variable_declarator":
                    inner = child.child_by_field_name("name")
                    if inner:
                        return self._text(inner, source)
        return None

    def _get_string_field(self, node, source: bytes, field: str) -> str | None:
        child = node.child_by_field_name(field)
        if child:
            text = self._text(child, source)
            return text.strip("'\"` ")
        return None

    def _find_child(self, node, types: frozenset[str]):
        for child in node.children:
            if child.type in types:
                return child
        return None

    def _iter_children(self, node, type_: str):
        for child in node.children:
            if child.type == type_:
                yield child

    def _extract_jsdoc(self, node, source: bytes) -> str | None:
        # Check node itself, then its parent (for exported declarations where comment
        # precedes the export_statement, not the inner function_declaration)
        candidates = [node]
        if node.parent and node.parent.type == _EXPORT_STATEMENT:
            candidates.append(node.parent)

        for candidate in candidates:
            parent = candidate.parent
            if not parent:
                continue
            prev = None
            for child in parent.children:
                if child == candidate:
                    break
                prev = child
            if prev and prev.type == "comment":
                text = self._text(prev, source)
                if text.startswith("/**"):
                    lines = text[3:-2].splitlines()
                    cleaned = " ".join(
                        line.strip().lstrip("*").strip()
                        for line in lines
                        if line.strip().lstrip("*").strip()
                    )
                    return cleaned or None
        return None

    def _detect_entrypoint(self, rel_path: str) -> bool:
        name = Path(rel_path).stem.lower()
        return name in {"index", "main", "app", "server", "client"}


class JavaScriptParser(TypeScriptParser):
    LANGUAGE = "javascript"
    EXTENSIONS = frozenset({".js", ".jsx", ".mjs", ".cjs"})

    def __init__(self) -> None:
        self._lang = Language(tsjs.language())
        self._parser = Parser(self._lang)
