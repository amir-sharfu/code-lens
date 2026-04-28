from pathlib import Path
from codelens.parsers.base import BaseParser
from codelens.parsers.python_parser import PythonParser
from codelens.parsers.typescript_parser import TypeScriptParser, JavaScriptParser

_REGISTRY: list[BaseParser] = [
    PythonParser(),
    TypeScriptParser(),
    JavaScriptParser(),
]


def get_parser_for(path: Path) -> BaseParser | None:
    for parser in _REGISTRY:
        if parser.supports(path):
            return parser
    return None


__all__ = ["BaseParser", "PythonParser", "TypeScriptParser", "JavaScriptParser", "get_parser_for"]
