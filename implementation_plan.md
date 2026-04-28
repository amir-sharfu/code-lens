 CodeLens — Full Implementation Plan                                                                                                                            
                                     
 Context                             

 LLMs struggle with large codebases because they either read everything (slow, expensive, confused) or grep blindly (misses structure). CodeLens solves this by
  stripping any repo to a compressed architectural representation (<5k tokens) and ranking files by structural importance via PageRank — so the AI gets a
 proper orientation before it starts working.

 Tech stack confirmed (Python 3.11.15, Windows 11, nothing installed yet):
 - tree-sitter + tree-sitter-languages — AST parsing (pre-built wheels for Windows, no compilation)
 - networkx — dependency graph + PageRank
 - pathspec — .gitignore handling
 - chromadb — vector store (Phase 3)
 - typer — CLI
 - pydantic v2 — data models
 - sqlalchemy — SQLite persistence
 - watchdog — file watching

 Agents to install from VoltAgent/awesome-claude-code-subagents categories/05-data-ai/:
 - data-engineer.md — pipeline architecture
 - data-scientist.md — embedding/vector search strategy
 - ml-engineer.md — production deployment
 - llm-architect.md — RAG/retrieval design

 ---
 Project Structure (all 5 phases)

 D:\claude_project\code-lens\
 ├── .claude\
 │   └── agents\
 │       ├── data-engineer.md
 │       ├── data-scientist.md
 │       ├── ml-engineer.md
 │       └── llm-architect.md
 ├── codelens\
 │   ├── __init__.py
 │   ├── models.py
 │   ├── walker.py
 │   ├── extractor.py
 │   ├── parsers\
 │   │   ├── __init__.py          ← parser registry
 │   │   ├── base.py
 │   │   ├── python_parser.py
 │   │   └── typescript_parser.py ← includes JavaScriptParser subclass
 │   ├── graph.py                 (Phase 2)
 │   ├── resolver.py              (Phase 2)
 │   ├── embeddings.py            (Phase 3)
 │   ├── vector_store.py          (Phase 3)
 │   ├── retriever.py             (Phase 3)
 │   ├── db\
 │   │   ├── schema.py            (Phase 4)
 │   │   ├── repository.py        (Phase 4)
 │   │   └── incremental.py       (Phase 4)
 │   ├── cli.py                   (Phase 5)
 │   ├── mcp_server.py            (Phase 5)
 │   └── config.py                (Phase 5)
 ├── tests\
 │   ├── fixtures\
 │   │   ├── sample_python.py
 │   │   ├── sample_typescript.ts
 │   │   └── sample_javascript.js
 │   ├── test_models.py
 │   ├── test_walker.py
 │   ├── test_python_parser.py
 │   ├── test_typescript_parser.py
 │   └── test_extractor.py
 ├── pyproject.toml
 ├── requirements.txt
 └── readme.md

 ---
 Phase 1: Skeleton Stripper ← Implement First

 Step 0 — Install agents + dependencies

 # Create agent directory and copy from VoltAgent repo
 mkdir D:\claude_project\code-lens\.claude\agents
 gh api repos/VoltAgent/awesome-claude-code-subagents/contents/categories/05-data-ai/data-engineer.md --jq '.content' | tr -d '\n' | base64 -d >
 .claude/agents/data-engineer.md
 # (repeat for data-scientist, ml-engineer, llm-architect)

 # Install dependencies
 pip install tree-sitter tree-sitter-languages pydantic pathspec pytest pytest-cov

 pyproject.toml:
 [build-system]
 requires = ["setuptools>=68", "wheel"]
 build-backend = "setuptools.backends.legacy:build"

 [project]
 name = "codelens"
 version = "0.1.0"
 requires-python = ">=3.11"
 dependencies = [
     "tree-sitter>=0.21",
     "tree-sitter-languages>=1.10",
     "pydantic>=2.5",
     "pathspec>=0.12",
 ]

 [project.optional-dependencies]
 dev = ["pytest>=7", "pytest-cov"]

 [tool.setuptools.packages.find]
 where = ["."]
 include = ["codelens*"]

 ---
 Step 1 — codelens/models.py (no internal deps)

 Key models:

 class ImportEntry(BaseModel):
     from_: str = Field(..., alias="from")   # alias so JSON uses "from" not "from_"
     symbols: list[str] = Field(default_factory=list)
     is_dynamic: bool = False
     model_config = {"populate_by_name": True}

 class SymbolEntry(BaseModel):
     kind: Literal["function", "class", "method", "variable", "type"]
     name: str
     signature: str      # full sig, no body
     doc: str | None = None
     line: int
     is_async: bool = False
     is_exported: bool = False

 class FileSkeleton(BaseModel):
     path: str           # repo-relative POSIX path
     language: str
     imports: list[ImportEntry]
     exports: list[str]
     symbols: list[SymbolEntry]
     loc: int
     is_entrypoint: bool = False
     is_auto_generated: bool = False
     def to_dict(self): return self.model_dump(by_alias=True)

 class RepoSkeleton(BaseModel):
     repo_path: str
     files: list[FileSkeleton]
     total_files: int = 0
     skipped_files: int = 0
     languages_found: list[str]

 ---
 Step 2 — codelens/parsers/base.py

 Abstract interface. Key contract: never raise on malformed source, return partial skeleton.

 class BaseParser(ABC):
     LANGUAGE: str = ""
     EXTENSIONS: frozenset[str] = frozenset()

     @abstractmethod
     def parse(self, source: bytes, rel_path: str) -> FileSkeleton: ...

     def supports(self, path: Path) -> bool:
         return path.suffix.lower() in self.EXTENSIONS

     # Shared: _count_lines, _strip_bom, _first_doc

 ---
 Step 3 — codelens/parsers/python_parser.py

 Uses Python's built-in ast module (no external dep, more reliable than tree-sitter for Python).

 Extraction logic:
 - _extract_imports → walk ast.Import and ast.ImportFrom, handle relative imports via "." * node.level
 - _extract_symbols → top-level FunctionDef, AsyncFunctionDef, ClassDef; methods one level deep inside classes; UPPER_CASE module-level Assign
 - _build_function_signature → use ast.unparse(node.args) + return annotation
 - _infer_exports → check __all__ list literal first; fallback to all public top-level names
 - _detect_entrypoint → if __name__ == "__main__" or filename in {main, app, server, wsgi, asgi, manage}

 ---
 Step 4 — codelens/parsers/typescript_parser.py

 Uses tree-sitter-languages (get_language("typescript") / get_parser("typescript")).

 Node types to handle:
 - import_statement → parse named_imports, namespace_import, source field
 - export_statement → export_clause, inline function_declaration/class_declaration/lexical_declaration/type_alias_declaration/interface_declaration
 - Signatures: take node_text.split("{")[0].strip() — avoids body, handles multiline sigs
 - JSDoc: walk backwards in parent's children to find preceding /** ... */ comment node
 - Entrypoint: filename stem in {index, main, app, server, client}

 JavaScriptParser is a thin subclass: same logic, get_language("javascript").

 ---
 Step 5 — codelens/walker.py

 _ALWAYS_SKIP_DIRS = frozenset({
     "node_modules", "venv", ".venv", "env",
     "dist", "build", ".next", ".nuxt", "__pycache__",
     ".git", ".svn", ".hg", "coverage", "htmlcov",
 })
 _SOURCE_EXTENSIONS = frozenset({".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})
 _AUTO_GEN_PATTERNS = (b"// AUTO-GENERATED", b"// Code generated", b"# AUTO-GENERATED",
                       b"// @generated", b"DO NOT EDIT", b"/* eslint-disable */")
 _MAX_LOC = 2000

 Key implementation notes:
 - Use dirnames[:] = [...] in-place mutation → os.walk prunes entire subtree (critical for node_modules)
 - Skip hidden dirs with .startswith(".") guard
 - Auto-gen check: read first 512 bytes for header, only count lines if no header match
 - All yielded paths: file_path.relative_to(root).as_posix() — guaranteed forward slashes on Windows

 def walk_repo(repo_path) -> Iterator[tuple[Path, str]]:
     # yields (absolute_path, repo_relative_posix_path)

 ---
 Step 6 — codelens/parsers/__init__.py (registry)

 _REGISTRY = [PythonParser(), TypeScriptParser(), JavaScriptParser()]

 def get_parser_for(path: Path) -> BaseParser | None:
     for parser in _REGISTRY:
         if parser.supports(path):
             return parser
     return None

 ---
 Step 7 — codelens/extractor.py

 def extract_repo(repo_path) -> RepoSkeleton:
     # Walk → get_parser_for → parser.parse → append to RepoSkeleton

 def extract_file(file_path, repo_root) -> FileSkeleton | None:
     # Single file extraction

 def to_json(skeleton: RepoSkeleton, indent=2) -> str:
     # json.dumps with by_alias=True for "from" keys

 ---
 Step 8 — Test Fixtures

 tests/fixtures/sample_python.py must include:
 - stdlib imports (import os, import sys)
 - relative imports (from .utils import helper_func, from ..config import settings)
 - __all__ = ["PublicClass", "public_function"]
 - CONSTANT = "value" (module-level constant)
 - class PublicClass with docstring, __init__, public method, _private_method
 - async def public_function(email: str, password: str) -> Optional[str] with docstring
 - def _private_function()
 - if __name__ == "__main__": pass

 tests/fixtures/sample_typescript.ts must include:
 - Named import: import { UserModel } from './db'
 - Default import: import bcrypt from 'bcrypt'
 - Type import: import type { Session } from './types'
 - export interface LoginOptions
 - export async function loginUser(...) with JSDoc
 - export class AuthService with constructor and method
 - export const TIMEOUT_MS = 3000
 - export type UserId = string
 - export * from './helpers' (re-export)

 tests/fixtures/sample_javascript.js must include:
 - const path = require('path') (CommonJS)
 - import express from 'express' (ESM)
 - export function createServer(port)
 - export const DEFAULT_PORT = 3000

 ---
 Step 9 — Test Files

 ┌───────────────────────────┬──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │           File            │                                                        Key assertions                                                        │
 ├───────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ test_models.py            │ ImportEntry alias serializes "from" not "from_"; to_dict() uses aliases                                                      │
 ├───────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ test_walker.py            │ Finds .py files; skips node_modules, venv; respects .gitignore; yields POSIX paths (no \)                                    │
 ├───────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ test_python_parser.py     │ Imports include stdlib + relative; exports from __all__; async function detected; entrypoint detected; syntax error returns  │
 │                           │ partial skeleton                                                                                                             │
 ├───────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ test_typescript_parser.py │ Named import symbols; re-export *; JSDoc on loginUser; interface as kind="type"; methods extracted                           │
 ├───────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ test_extractor.py         │ Multi-language repo; POSIX paths in output; "from" key in JSON (not "from_"); unsupported files return None                  │
 └───────────────────────────┴──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

 ---
 Phase 1 Validation Checkpoint

 # All tests green
 pytest tests/ -v --tb=short

 # Self-parse the project
 python -c "from codelens import extract_repo, to_json; print(to_json(extract_repo('.')))"

 # Verify: no backslashes in paths, no node_modules entries

 ---
 Phase 2: Dependency Graph (implement after Phase 1 validated)

 New files: codelens/resolver.py, codelens/graph.py
 New dep: pip install networkx>=3.2

 resolver.py: resolve(import_from, importing_file_posix, repo_root) -> str | None
 - If starts with . → relative resolution (try .py, .ts, /index.ts, /index.py, etc.)
 - External packages (no . or / prefix) → return None
 - Normalize to lowercase for Windows case-insensitivity

 graph.py:
 - build_graph(skeleton) -> nx.DiGraph — nodes=files, edges=imports
 - compute_importance(graph, skeleton) -> dict[str, float]:
 0.5 * pagerank[file]              # nx.pagerank(alpha=0.85, max_iter=100, tol=1e-4)
 + 0.2 * normalize(in_degree)
 + 0.2 * normalize(loc)
 + 0.1 * is_entrypoint
 - assign_tiers(importance) -> dict[str, str] — percentile buckets: Core(≥90%), Important(≥70%), Supporting(≥40%), Peripheral

 ---
 Phase 3: Smart Retrieval (implement after Phase 2 validated)

 New files: codelens/embeddings.py, codelens/vector_store.py, codelens/retriever.py
 New deps: pip install chromadb>=0.4 sentence-transformers>=2.7

 Embedding backends (pluggable via CODELENS_EMBEDDING_BACKEND=local|openai):
 - LocalEmbeddingBackend: bge-small-en-v1.5 (384-dim, CPU, free)
 - OpenAIEmbeddingBackend: text-embedding-3-small (requires OPENAI_API_KEY)

 What to embed: one chunk per symbol (signature + doc); one per file (all exports summary)

 Retrieval algorithm (in retriever.py):
 1. vector_store.query(query, k=20) — semantic recall
 2. Re-rank: score *= (1 + importance_scores[file])
 3. Expand top-5: add 1-hop graph neighbors
 4. pack_context(files, max_tokens) — importance-descending order, token budget via len // 4

 ---
 Phase 4: Persistence Layer (implement after Phase 3 validated)

 New files: codelens/db/schema.py, codelens/db/repository.py, codelens/db/incremental.py
 New dep: pip install sqlalchemy>=2.0

 SQLite location: {repo_root}/.codelens/index.db

 Schema (3 tables):
 - files(path PK, skeleton_json, importance_score, tier, last_modified, content_hash)
 - dependencies(from_file, to_file, import_type, PK(from,to))
 - observations(id, file_path FK, session_id, observation_type, content, importance, created_at) — observations inherit importance from file tier at insert
 time

 Incremental update: sha256(bytes).hexdigest() per file; only re-parse changed files; recompute PageRank every 10 changes or on init --full

 ---
 Phase 5: CLI + MCP Server (implement after Phase 4 validated)

 New files: codelens/cli.py, codelens/mcp_server.py, codelens/config.py
 New deps: pip install typer>=0.12 watchdog>=4.0 mcp>=1.0

 CLI commands:
 codelens init [PATH]          # scan → build graph → DB + vector store
 codelens query TEXT           # hybrid retrieval, print context
 codelens watch [PATH]         # watchdog daemon, incremental updates
 codelens map [--format mermaid|json]
 codelens stats                # tier distribution table

 MCP tools:
 - get_relevant_files(query, max_tokens=4000) — packed context string
 - get_file_skeleton(path) — FileSkeleton JSON
 - get_dependency_subgraph(file, depth=2) — nodes + edges JSON

 MCP uses mcp Python SDK, runs over stdio (works in Claude Desktop + Cursor without port config).

 ---
 Implementation Order Within Each Phase

 Phase 1 sequence (order matters for imports):
 1. pyproject.toml, requirements.txt
 2. Install agents to .claude/agents/
 3. codelens/__init__.py (empty stub)
 4. codelens/models.py
 5. codelens/parsers/base.py
 6. codelens/parsers/python_parser.py
 7. codelens/parsers/typescript_parser.py
 8. codelens/parsers/__init__.py (registry)
 9. codelens/walker.py
 10. codelens/extractor.py
 11. codelens/__init__.py (fill public API)
 12. tests/fixtures/ (static sample files)
 13. tests/test_models.py, test_walker.py, test_python_parser.py, test_typescript_parser.py, test_extractor.py
 14. Run pytest tests/ -v — all green before declaring Phase 1 done

 ---
 Known Limitations to Document

 1. Dynamic imports (require(variable)) silently skipped — is_dynamic flag set only for statically detectable patterns
 2. Re-exports (export * from './foo') captured as "*" — symbol expansion deferred to Phase 2 resolver
 3. Python decorator text not included in signature field
 4. TypeScript type-only imports captured without is_type_only flag
 5. Monorepo cross-package imports unresolved until Phase 2 adds workspace-aware resolver