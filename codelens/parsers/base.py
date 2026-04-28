from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from codelens.models import FileSkeleton


class BaseParser(ABC):
    LANGUAGE: str = ""
    EXTENSIONS: frozenset[str] = frozenset()

    @abstractmethod
    def parse(self, source: bytes, rel_path: str) -> FileSkeleton:
        """Parse source bytes and return a FileSkeleton. Never raises on malformed source."""
        ...

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in self.EXTENSIONS

    @staticmethod
    def _count_lines(source: bytes) -> int:
        return source.count(b"\n") + (1 if source else 0)

    @staticmethod
    def _strip_bom(source: bytes) -> bytes:
        return source.lstrip(b"\xef\xbb\xbf")

    @staticmethod
    def _first_doc(text: str) -> str | None:
        stripped = text.strip()
        return stripped if stripped else None
