# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Python Runtime

Always use the explicit interpreter path — `python` resolves to the Microsoft Store stub on this machine:

```
C:/Users/hp/AppData/Local/Python/pythoncore-3.14-64/python.exe
```

## Commands

```bash
# Install core deps + dev tools
C:/Users/hp/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pip install -e ".[dev]"

# Install everything (all phases)
C:/Users/hp/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pip install -e ".[dev,all]"

# Run all tests
C:/Users/hp/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest tests/ -v

# Run a single test file
C:/Users/hp/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest tests/test_graph.py -v

# Run a single test by name
C:/Users/hp/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest tests/ -k "test_hub_file" -v

# Run with coverage
C:/Users/hp/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest tests/ --cov=codelens --cov-report=term-missing

# Full pipeline smoke test (Phases 1–2)
C:/Users/hp/AppData/Local/Python/pythoncore-3.14-64/python.exe -c "
from codelens import extract_repo, build_and_score
from codelens.compact_repr import compact_repr
skeleton = extract_repo('.')
graph, importance, tiers = build_and_score(skeleton)
print(compact_repr(skeleton, importance=importance))
"

# CLI (after pip install -e .[phase4,phase5])
codelens init .
codelens stats .
codelens map . --format json
codelens query "find auth logic"

# MCP server (set CODELENS_REPO_PATH first)
C:/Users/hp/AppData/Local/Python/pythoncore-3.14-64/python.exe -m codelens.mcp_server
```

## Architecture

CodeLens extracts compressed architectural skeletons from codebases so LLMs get structural orientation before working in unfamiliar code. The full pipeline runs across five phases:

```
walk_repo()  →  parser.parse()  →  FileSkeleton  →  RepoSkeleton
  →  build_graph()  →  compute_importance()  →  assign_tiers()
  →  compact_repr(skeleton, importance=scores)  →  str (<5K tokens)
  →  VectorStore.upsert_file()  →  retrieve(query, ...)  →  packed context
  →  IncrementalUpdater  →  SQLite DB (.codelens/index.db)
  →  CLI / MCP server
```

**Phase 1** — `walker.py`, `parsers/`, `extractor.py`, `models.py`, `compact_repr.py`
**Phase 2** — `resolver.py`, `graph.py`
**Phase 3** — `embeddings.py`, `vector_store.py`, `retriever.py`
**Phase 4** — `db/schema.py`, `db/repository.py`, `db/incremental.py`
**Phase 5** — `config.py`, `cli.py`, `mcp_server.py`

## Key Design Decisions

**Models (`models.py`)**
- `ImportEntry` uses `Field(alias="from")` — JSON output uses `"from"` (a Python keyword). Always serialize with `model_dump(by_alias=True)` or `to_dict()`. Construct with `ImportEntry(**{"from": "..."})` or `ImportEntry(from_="...")` — both work due to `populate_by_name=True`.
- Parsers never raise on malformed source — return a partial `FileSkeleton` (empty imports/symbols, correct `loc`). Hard contract in `BaseParser`.

**Parsers (`parsers/`)**
- `PythonParser` uses stdlib `ast`, not tree-sitter.
- `TypeScriptParser` uses tree-sitter 0.25.x API: `Language(tsts.language_typescript())` / `Parser(lang)`. The old `tree-sitter-languages` `get_language()`/`get_parser()` is not available for Python 3.14 — do not use it.
- `JavaScriptParser` subclasses `TypeScriptParser`, only overrides `__init__` to load the JS grammar.

**Walker (`walker.py`)**
- Uses `dirnames[:] = [...]` in-place mutation so `os.walk` prunes entire subtrees (critical for `node_modules`).
- All yielded paths are POSIX via `.as_posix()` — guaranteed on Windows.
- Auto-generated detection: read first 512 bytes for header patterns; only count newlines if no header matched. Files with >5000 newlines are skipped.

**Resolver (`resolver.py`)**
- Uses `posixpath.normpath` to collapse `..` segments. `PurePosixPath` does NOT normalize `..` — don't use it for path arithmetic.
- Returns `None` for external packages (no `.` or `/` prefix), unresolvable paths, or files not found in the repo.

**Graph (`graph.py`)**
- Edge convention: A→B means "A imports B". PageRank runs on the **reversed** graph so heavily-imported files score highest.
- Pure-Python power-iteration PageRank (`_pagerank_python`) — avoids scipy dependency. Do not call `nx.pagerank` directly; networkx 3.6+ will try scipy when numpy is present.
- Composite importance: `0.5 * pr * len(graph) + 0.2×normalized_in_degree + 0.2×normalized_loc + 0.1×is_entrypoint`, normalized to [0,1]. The `* len(graph)` scales raw PageRank values (which sum to 1.0, so each is ~1/N) into a range comparable to the other [0,1] components.
- Tier thresholds are percentile-based: 90th=core, 70th=important, 40th=supporting, else peripheral.

**Embeddings (`embeddings.py`)**
- All heavy imports (`sentence_transformers`, `openai`) are lazy — inside `__init__`. The module loads without deps installed.
- Select backend via `CODELENS_EMBEDDING_BACKEND=local|openai` (default: `local`).
- `LocalEmbeddingBackend` uses `BAAI/bge-small-en-v1.5`; override with `CODELENS_EMBED_MODEL`.

**Vector Store (`vector_store.py`)**
- `chunks_for_file(f)` is a module-level function — testable independently of ChromaDB.
- `VectorStore` accepts `_client=` for dependency injection in tests (avoids real ChromaDB).
- `delete_file(path)` removes by metadata `where={"path": path}` — used during incremental updates.

**Retriever (`retriever.py`)**
- Pipeline: `vector_store.query(k=20)` → re-rank `score × (1 + importance)` → expand top-5 with 1-hop graph neighbours → `pack_context(max_tokens)`.
- `pack_context` budget: `char_budget = max_tokens * 4`.

**DB Layer (`db/`)**
- `IncrementalUpdater` always creates the parent directory of `db_path` — safe to pass any path.
- `FileSkeleton` is stored as JSON via `to_dict()` (aliased). Deserialise with `FileSkeleton.model_validate(json.loads(rec.skeleton_json))` — pydantic v2 respects aliases on `model_validate`.
- PageRank is recomputed when `changes >= RECOMPUTE_THRESHOLD` (10) or `full=True`. Below threshold only parses changed files; importance scores in DB stay stale until next recompute.
- `ObservationRepository.add()` inherits `importance_score` from the referenced `FileRecord` when `obs.importance == 0.0`.

**CLI (`cli.py`)**
- Boolean flag pattern: `typer.Option(True, "--full/--incremental")` — negation flag is `--incremental`, not `--no-full`.
- `query` falls back to `compact_repr` (structural only) when phase3 deps are not installed or the vector store is empty.
- `map` sanitises node IDs for Mermaid by replacing `/` and `.` with `_`.

**MCP Server (`mcp_server.py`)**
- Tool logic lives in `get_relevant_files_impl`, `get_file_skeleton_impl`, `get_dependency_subgraph_impl` — test these directly without the MCP protocol.
- `_bfs_subgraph(graph, root, depth)` expands both predecessors and successors at each hop.
- Repo path: `CODELENS_REPO_PATH` env var (defaults to cwd).

**Config (`config.py`)**
- `CodeLensConfig.for_repo(path?)` is the single entry point for all path/env resolution.
- `is_initialized` checks only for the DB file — the vector store may still be empty.

## Public API

```python
# Phase 1
from codelens import extract_repo, extract_file, to_json

# Phase 2
from codelens import build_graph, compute_importance, assign_tiers, build_and_score

# Phase 3 (pip install codelens[phase3])
from codelens import get_embedding_backend, VectorStore, retrieve, pack_context

# Phase 4 (pip install codelens[phase4])
from codelens import IncrementalUpdater, FileRepository, DependencyRepository, ObservationRepository

# Phase 5 (pip install codelens[phase5])
from codelens import CodeLensConfig
```

## Adding a New Language Parser

1. Create `codelens/parsers/<lang>_parser.py` subclassing `BaseParser`
2. Set `LANGUAGE`, `EXTENSIONS`, implement `parse(source: bytes, rel_path: str) -> FileSkeleton`
3. Register an instance in `codelens/parsers/__init__.py` `_REGISTRY`
4. Add fixture to `tests/fixtures/` and test file `tests/test_<lang>_parser.py`

## Project Agents

Four Claude Code sub-agents are installed in `.claude/agents/`:
- `data-engineer` — pipeline/ETL architecture
- `data-scientist` — embedding and vector search strategy
- `ml-engineer` — production deployment patterns
- `llm-architect` — RAG and retrieval design
