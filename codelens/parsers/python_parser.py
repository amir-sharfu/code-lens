from __future__ import annotations
import ast
from pathlib import Path
from codelens.parsers.base import BaseParser
from codelens.models import FileSkeleton, ImportEntry, SymbolEntry


class PythonParser(BaseParser):
    LANGUAGE = "python"
    EXTENSIONS = frozenset({".py", ".pyi"})

    def parse(self, source: bytes, rel_path: str) -> FileSkeleton:
        source = self._strip_bom(source)
        loc = self._count_lines(source)
        skeleton = FileSkeleton(path=rel_path, language=self.LANGUAGE, loc=loc)

        try:
            tree = ast.parse(source, filename=rel_path)
        except SyntaxError:
            return skeleton

        skeleton.imports = self._extract_imports(tree)
        skeleton.symbols = self._extract_symbols(tree)
        skeleton.exports = self._infer_exports(tree)
        skeleton.is_entrypoint = self._detect_entrypoint(tree, rel_path)
        return skeleton

    def _extract_imports(self, tree: ast.Module) -> list[ImportEntry]:
        entries: list[ImportEntry] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    entries.append(ImportEntry(**{"from": alias.name, "symbols": []}))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                prefix = "." * (node.level or 0)
                symbols = [a.name for a in node.names]
                if symbols == ["*"]:
                    symbols = []
                entries.append(ImportEntry(**{"from": prefix + module, "symbols": symbols}))
        return entries

    def _extract_symbols(self, tree: ast.Module) -> list[SymbolEntry]:
        symbols: list[SymbolEntry] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(self._function_symbol(node, is_method=False))
            elif isinstance(node, ast.ClassDef):
                symbols.append(self._class_symbol(node))
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        symbols.append(self._function_symbol(item, is_method=True))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        symbols.append(SymbolEntry(
                            kind="variable",
                            name=target.id,
                            signature=target.id,
                            line=node.lineno,
                        ))
        return symbols

    def _function_symbol(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        is_method: bool,
    ) -> SymbolEntry:
        sig = self._build_function_signature(node)
        doc = self._first_doc(ast.get_docstring(node) or "")
        return SymbolEntry(
            kind="method" if is_method else "function",
            name=node.name,
            signature=sig,
            doc=doc,
            line=node.lineno,
            is_async=isinstance(node, ast.AsyncFunctionDef),
            is_exported=not node.name.startswith("_"),
        )

    def _class_symbol(self, node: ast.ClassDef) -> SymbolEntry:
        bases = ", ".join(ast.unparse(b) for b in node.bases)
        sig = f"class {node.name}({bases})" if bases else f"class {node.name}"
        doc = self._first_doc(ast.get_docstring(node) or "")
        return SymbolEntry(
            kind="class",
            name=node.name,
            signature=sig,
            doc=doc,
            line=node.lineno,
            is_exported=not node.name.startswith("_"),
        )

    def _build_function_signature(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> str:
        args = ast.unparse(node.args)
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        return f"{prefix} {node.name}({args}){ret}"

    def _infer_exports(self, tree: ast.Module) -> list[str]:
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "__all__"
            ):
                if isinstance(node.value, (ast.List, ast.Tuple)):
                    return [
                        elt.value
                        for elt in node.value.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    ]
        public: list[str] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    public.append(node.name)
        return public

    def _detect_entrypoint(self, tree: ast.Module, rel_path: str) -> bool:
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == "__name__"
                and len(node.test.comparators) == 1
                and isinstance(node.test.comparators[0], ast.Constant)
                and node.test.comparators[0].value == "__main__"
            ):
                return True
        name = Path(rel_path).stem.lower()
        return name in {"main", "app", "server", "wsgi", "asgi", "manage"}
