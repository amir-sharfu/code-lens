from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class ImportEntry(BaseModel):
    from_: str = Field(..., alias="from")
    symbols: list[str] = Field(default_factory=list)
    is_dynamic: bool = False

    model_config = {"populate_by_name": True}


class SymbolEntry(BaseModel):
    kind: Literal["function", "class", "method", "variable", "type"]
    name: str
    signature: str
    doc: str | None = None
    line: int
    is_async: bool = False
    is_exported: bool = False


class FileSkeleton(BaseModel):
    path: str
    language: str
    imports: list[ImportEntry] = Field(default_factory=list)
    exports: list[str] = Field(default_factory=list)
    symbols: list[SymbolEntry] = Field(default_factory=list)
    loc: int = 0
    is_entrypoint: bool = False
    is_auto_generated: bool = False

    def to_dict(self) -> dict:
        return self.model_dump(by_alias=True)


class RepoSkeleton(BaseModel):
    repo_path: str
    files: list[FileSkeleton] = Field(default_factory=list)
    total_files: int = 0
    skipped_files: int = 0
    languages_found: list[str] = Field(default_factory=list)

    def get_file(self, path: str) -> FileSkeleton | None:
        for f in self.files:
            if f.path == path:
                return f
        return None
