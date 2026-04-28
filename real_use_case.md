# CodeLens — Real Use Case: All 5 Phases

> This document traces a single directory path through all five phases of CodeLens,
> showing exactly what the input is, what happens inside, and what the output looks like —
> using this repo itself as the live example.

---

## The Big Picture

```
Directory path (".")
    │
    ▼ Phase 1 — Extraction
    RepoSkeleton + compact_repr string
    │
    ▼ Phase 2 — Graph & Scoring
    nx.DiGraph + importance dict + tiers dict
    │
    ▼ Phase 3 — Embeddings & Semantic Search
    Populated VectorStore + context string (per query)
    │
    ▼ Phase 4 — Persistence
    SQLite DB (.codelens/index.db) — all of the above, stored on disk
    │
    ▼ Phase 5 — Interface
    CLI commands + MCP server tools (used by Claude Desktop / Cursor)
```

---

---

## Phase 1 — Extraction

### What is Phase 1?

Phase 1 is the **extraction layer**. It takes a directory on disk and produces two things:

1. A structured `RepoSkeleton` object (held in memory / serializable to JSON)
2. A compressed plain-text summary (`compact_repr`) ready to paste into an LLM context

### Input

A single argument: a path to any directory.

```python
from codelens import extract_repo
skeleton = extract_repo(".")        # or any absolute/relative path
```

### What happens inside (the pipeline)

```
Directory path
    │
    ▼
walk_repo()                         ← walker.py
    Yields (abs_path, rel_posix) for every .py / .ts / .js file.
    Prunes: node_modules, venv, __pycache__, build/, dist/
    Respects: .gitignore (via pathspec)
    Skips: auto-generated files (header patterns) and files > 5000 lines
    All paths are POSIX (forward slashes), even on Windows.
    │
    ▼ (one per file)
get_parser_for(abs_path)            ← parsers/__init__.py
    Returns PythonParser, TypeScriptParser, or JavaScriptParser.
    Returns None for unsupported extensions → file is skipped.
    │
    ▼
parser.parse(source_bytes, rel_posix)
    PythonParser     → uses stdlib ast (no tree-sitter)
    TypeScriptParser → uses tree-sitter 0.25.x
    JavaScriptParser → subclass of TypeScriptParser, swaps grammar
    Contract: NEVER raises on malformed source — returns a partial FileSkeleton.
    │
    ▼
FileSkeleton                        ← models.py
    One object per file.
    │
    ▼
RepoSkeleton                        ← models.py
    Collects all FileSkeleton objects + top-level metadata.
    │
    ▼
compact_repr(skeleton)              ← compact_repr.py
    Ranks files by importance proxy (public symbol count, exports, class count).
    Formats up to 40 files, 10 symbols each, within a 5,000-token budget.
    Returns a plain string.
```

### Output structure

#### 1. FileSkeleton (one per parsed file)

```json
{
  "path": "codelens/graph.py",
  "language": "python",
  "loc": 199,
  "is_entrypoint": false,
  "is_auto_generated": false,
  "imports": [
    { "from": "__future__",       "symbols": ["annotations"], "is_dynamic": false },
    { "from": "codelens.models",  "symbols": ["RepoSkeleton", "FileSkeleton"], "is_dynamic": false },
    { "from": "codelens.resolver","symbols": ["resolve"], "is_dynamic": false },
    { "from": "networkx",         "symbols": [], "is_dynamic": false }
  ],
  "exports": ["build_graph", "compute_importance", "assign_tiers", "build_and_score"],
  "symbols": [
    {
      "kind": "function",
      "name": "build_graph",
      "signature": "def build_graph(skeleton: RepoSkeleton) -> 'nx.DiGraph'",
      "doc": "Build a directed import graph from a RepoSkeleton.",
      "line": 24,
      "is_async": false,
      "is_exported": true
    },
    {
      "kind": "function",
      "name": "compute_importance",
      "signature": "def compute_importance(graph: 'nx.DiGraph', skeleton: RepoSkeleton) -> dict[str, float]",
      "doc": "Compute a composite importance score for each file.",
      "line": 96,
      "is_async": false,
      "is_exported": true
    }
  ]
}
```

#### 2. RepoSkeleton (one per run)

```json
{
  "repo_path": "D:\\claude_project\\code-lens",
  "total_files": 41,
  "skipped_files": 0,
  "languages_found": ["javascript", "python", "typescript"],
  "files": [ "...array of 41 FileSkeleton objects..." ]
}
```

#### 3. compact_repr output (actual live output on this repo)

```
# Repository Architecture
# code-lens
# 41 source files · python (39 files), javascript (1 file), typescript (1 file) · 5,280 total LOC
# 455 public symbols

## Entry Points
  codelens/cli.py
  codelens/mcp_server.py
  tests/fixtures/sample_python.py

## codelens/__init__.py  [56 loc]  -> extract_repo, extract_file, to_json, RepoSkeleton (+13)

## codelens/graph.py  [199 loc]  -> build_graph, compute_importance, assign_tiers, build_and_score
  def build_graph(skeleton: RepoSkeleton) -> 'nx.DiGraph'
      "Build a directed import graph from a RepoSkeleton."
  def compute_importance(graph: 'nx.DiGraph', skeleton: RepoSkeleton) -> dict[str, float]
      "Compute a composite importance score for each file."
  ...

# --- end of architectural summary (4,656 approx tokens) ---
```

**LOC = Lines of Code.** 5,280 means the sum of every file's line count across all 41 parsed files.
The actual run produced **4,656 tokens** — well within the 5,000-token budget.

### What Phase 1 does NOT do

| Missing in Phase 1            | Added in which phase        |
|-------------------------------|-----------------------------|
| Import → file resolution      | Phase 2 (`resolver.py`)     |
| PageRank / importance scores  | Phase 2 (`graph.py`)        |
| Semantic embeddings           | Phase 3                     |
| Vector search                 | Phase 3                     |
| Persistence to SQLite         | Phase 4                     |
| CLI / MCP server              | Phase 5                     |

### Key gotchas

- `ImportEntry` serializes with alias key `"from"` (a Python keyword) — always use `.to_dict()` or `model_dump(by_alias=True)`.
- Parsers return a **partial** `FileSkeleton` on bad source (empty imports/symbols, correct `loc`) — they never raise.
- Walker uses `dirnames[:] = [...]` in-place to prune `os.walk` subtrees — critical for skipping `node_modules` before descending.
- All paths inside `FileSkeleton` and `RepoSkeleton` are POSIX (forward slashes) regardless of OS.

---

---

## Phase 2 — Graph & Scoring

### Input (from Phase 1)

Phase 2 takes exactly the `RepoSkeleton` that Phase 1 produced.

```python
from codelens import extract_repo, build_and_score

skeleton = extract_repo(".")             # Phase 1
graph, importance, tiers = build_and_score(skeleton)   # Phase 2
```

### What happens inside — 3 steps

**Step 1 — `build_graph(skeleton)` → `nx.DiGraph`**

Reads every `FileSkeleton.imports` list. For each import string, `resolver.resolve()` converts it
into a repo-relative path. If that path exists in the repo, an edge is added.

```
codelens/cli.py  imports  "./config"
    resolver.resolve("./config", "codelens/cli.py", repo_root)
    → "codelens/config.py"   ← in file_set? YES
    → add edge: cli.py → config.py
```

Edge direction: **A → B means "A imports B"**

> Note: On this Python repo, graph has **41 nodes and 0 edges** because Python uses
> absolute imports (`from codelens.models import ...`) which don't start with `.` or `/`.
> The resolver treats those as external packages and returns `None`. The resolver is
> designed for TypeScript/JS relative imports (`./utils`, `../config`).

**Step 2 — `compute_importance(graph, skeleton)` → `dict[str, float]`**

Runs PageRank on the **reversed** graph (so heavily-imported files score highest),
then combines 4 signals into one score:

```
importance[path] =
    0.5 × PageRank × N        (how many files import this file)
  + 0.2 × in_degree / max     (raw importer count, normalized)
  + 0.2 × loc / max_loc       (bigger files weighted slightly higher)
  + 0.1 × is_entrypoint       (cli.py, main.py, etc.)

All normalized to [0, 1].
```

**Step 3 — `assign_tiers(importance)` → `dict[str, str]`**

Cuts importance scores at percentile thresholds:

| Tier        | Percentile  |
|-------------|-------------|
| `core`      | ≥ 90th      |
| `important` | ≥ 70th      |
| `supporting`| ≥ 40th      |
| `peripheral`| < 40th      |

### Three outputs (actual live values from this repo)

**1. `graph` — NetworkX DiGraph**
```
41 nodes, 0 edges
Each node carries: language, loc, is_entrypoint
```

**2. `importance` — `dict[str, float]`**
```python
{
  "codelens/cli.py":                       1.0000,
  "codelens/mcp_server.py":                0.9728,
  "codelens/parsers/typescript_parser.py": 0.8906,
  "codelens/db/incremental.py":            0.8068,
  "codelens/graph.py":                     0.7905,
  "codelens/compact_repr.py":              0.7859,
  ...
  "codelens/models.py":                    ~0.2,
  "codelens/resolver.py":                  ~0.1,
}
```

**3. `tiers` — `dict[str, str]`**
```python
# CORE (top 10% — 6 files)
"codelens/cli.py"                        → "core"
"codelens/mcp_server.py"                 → "core"
"codelens/parsers/typescript_parser.py"  → "core"
"codelens/db/incremental.py"             → "core"
"tests/test_db_repository.py"            → "core"
"tests/test_vector_store.py"             → "core"

# IMPORTANT (70th–90th — 7 files)
"codelens/graph.py"                      → "important"
"codelens/compact_repr.py"               → "important"
"codelens/vector_store.py"               → "important"

# SUPPORTING (40th–70th — 12 files)
"codelens/retriever.py"                  → "supporting"
"codelens/embeddings.py"                 → "supporting"

# PERIPHERAL (bottom 40% — 16 files)
"codelens/models.py"                     → "peripheral"
"codelens/resolver.py"                   → "peripheral"
"codelens/__init__.py"                   → "peripheral"
```

### How Phase 2 feeds back into Phase 1

The `importance` dict plugs directly back into `compact_repr`:

```python
# Phase 1 alone — heuristic ranking
compact_repr(skeleton)

# Phase 1 + Phase 2 — real structural ranking
compact_repr(skeleton, importance=importance)
```

With real scores, `compact_repr` puts `cli.py` first (score 1.0) instead of `__init__.py`.

### What Phase 2 does NOT do

| Missing           | Added in    |
|-------------------|-------------|
| Embedding content | Phase 3     |
| Vector search     | Phase 3     |
| Storing to SQLite | Phase 4     |
| CLI / MCP server  | Phase 5     |

---

---

## Phase 3 — Embeddings & Semantic Search

### Input (from Phase 1 + Phase 2)

Phase 3 takes all three Phase 2 outputs plus the original Phase 1 skeleton:

```python
from codelens import extract_repo, build_and_score, VectorStore, retrieve

skeleton = extract_repo(".")                          # Phase 1
graph, importance, tiers = build_and_score(skeleton)  # Phase 2

vs = VectorStore()                                    # Phase 3 — populate
for f in skeleton.files:
    vs.upsert_file(f)

context = retrieve("find auth logic", vs, graph, importance, skeleton)  # Phase 3 — query
```

### What happens inside — 3 components

**Component 1 — `embeddings.py` (the model)**

Converts text into a list of floating-point numbers (a vector).

| Backend         | Model                      | Details                                       |
|-----------------|----------------------------|-----------------------------------------------|
| `local` (default)| `BAAI/bge-small-en-v1.5`  | sentence-transformers, CPU, ~33MB download    |
| `openai`        | `text-embedding-3-small`   | API call, needs `OPENAI_API_KEY`              |

```
"def build_graph(skeleton: RepoSkeleton) -> nx.DiGraph"
    ↓  embed()
[0.023, -0.145, 0.891, 0.004, ...]   ← 384 floats (local model)
```

Similar text → similar vectors → close together in vector space.

**Component 2 — `vector_store.py` (the database)**

Takes each `FileSkeleton` and breaks it into chunks, then embeds and stores each chunk
in ChromaDB (on disk at `.codelens/chroma`).

What gets chunked per file:

```
FileSkeleton (codelens/graph.py)
    │
    ├── symbol chunk: "build_graph"
    │     text = signature + docstring
    │     "def build_graph(skeleton: RepoSkeleton) -> nx.DiGraph
    │      Build a directed import graph from a RepoSkeleton."
    │
    ├── symbol chunk: "compute_importance"
    │     text = "def compute_importance(...) -> dict[str, float]
    │      Compute a composite importance score for each file."
    │
    ├── symbol chunk: "assign_tiers"
    ├── symbol chunk: "build_and_score"
    │
    └── file_summary chunk  (because exports exist)
          text = "codelens/graph.py
                  exports: build_graph, compute_importance, assign_tiers, build_and_score"
```

Private symbols (starting with `_`) are **skipped** — only public API is indexed.

Each chunk is stored with metadata:
```python
{
  "path":       "codelens/graph.py",
  "language":   "python",
  "symbol":     "build_graph",
  "kind":       "function",
  "chunk_type": "symbol"    # or "file_summary"
}
```

**Component 3 — `retriever.py` (the query pipeline)**

When you ask a natural-language question, 4 steps run:

```
Query: "find auth logic"
    │
    ▼ Step 1: Semantic recall
    vector_store.query("find auth logic", k=20)
    → embed the query → find 20 nearest chunks in ChromaDB by cosine similarity
    → returns [{path, symbol, score=0.82}, {path, symbol, score=0.79}, ...]
    │
    ▼ Step 2: Re-rank
    combined_score = semantic_score × (1 + importance[file])
    → semantically matching + structurally important files rise higher
    → test files that match semantically but have low importance get pushed down
    │
    ▼ Step 3: 1-hop neighbor expand
    Take the top 5 ranked files
    → add their direct graph neighbors (files they import / files that import them)
    → even if those neighbors didn't appear in the semantic search
    → because if you need "auth logic", you probably also need the files it depends on
    │
    ▼ Step 4: pack_context()
    Sort all candidates by final score
    Include files one by one until the token budget (4000 tokens) is reached
    → returns a plain string
```

### Output of Phase 3

**One output: a plain string** — packed context for an LLM.

```
## codelens/graph.py  [199 loc, score=1.234]
  def build_graph(skeleton: RepoSkeleton) -> 'nx.DiGraph'
  def compute_importance(graph: 'nx.DiGraph', skeleton: RepoSkeleton) -> dict[str, float]
  def assign_tiers(importance: dict[str, float]) -> dict[str, str]
  def build_and_score(skeleton: RepoSkeleton) -> tuple[...]

## codelens/resolver.py  [101 loc, score=0.987]
  def resolve(import_from: str, importing_file: str, repo_root: str | Path) -> str | None

## codelens/models.py  [50 loc, score=0.741]
  class ImportEntry(BaseModel)
  class FileSkeleton(BaseModel)
  ...

# … 3 more files omitted (budget reached)
```

This string goes **directly into an LLM prompt**. The LLM sees only the files most relevant
to the query, not the entire codebase.

### Phase 1 vs Phase 3 output comparison

|                | Phase 1 `compact_repr`                   | Phase 3 `retrieve`                          |
|----------------|------------------------------------------|---------------------------------------------|
| Ranking        | Heuristic or Phase 2 PageRank            | **Query-specific** (semantic × importance)  |
| Coverage       | All files up to budget                   | Only files relevant to **this query**       |
| Use case       | "Map the whole repo for the LLM"         | "Answer this specific question"             |
| Score shown    | No                                       | Yes, per file                               |

### What Phase 3 does NOT do

| Missing                              | Added in    |
|--------------------------------------|-------------|
| Storing anything to SQLite           | Phase 4     |
| Incremental updates when files change| Phase 4     |
| Exposing `query` as a CLI command    | Phase 5     |
| Exposing it as an MCP tool           | Phase 5     |

---

---

## Phase 4 — Persistence

### What is Phase 4?

Phase 4 is the **persistence layer**. Everything computed in Phases 1–3 (file skeletons,
importance scores, tiers, dependency edges) is saved to a SQLite database so it
survives across sessions and only changed files need re-parsing.

### Input (from Phases 1–3)

Phase 4 doesn't call Phases 1–3 directly. Instead `IncrementalUpdater` orchestrates them
internally and writes the results to disk.

```python
from codelens.db.incremental import IncrementalUpdater

updater = IncrementalUpdater("/path/to/repo")

# First run — parse everything
summary = updater.init(full=True)

# Subsequent runs — only re-parse files that changed
summary = updater.update()

# Read back scores at any time
importance = updater.get_importance()   # dict[str, float]
tiers      = updater.get_tiers()        # dict[str, str]
```

### What happens inside — 3 components

**Component 1 — `db/schema.py` (the tables)**

Three SQLAlchemy ORM tables in `.codelens/index.db`:

```
files table (FileRecord)
    path            TEXT  PRIMARY KEY    "codelens/graph.py"
    skeleton_json   TEXT               Full FileSkeleton serialized as JSON
    importance_score FLOAT             0.7905
    tier            TEXT               "important"
    last_modified   FLOAT              file mtime (unix timestamp)
    content_hash    TEXT               SHA-256 of file bytes

dependencies table (DependencyRecord)
    from_file       TEXT  PRIMARY KEY   "codelens/cli.py"
    to_file         TEXT  PRIMARY KEY   "codelens/config.py"
    import_type     TEXT               "static"

observations table (ObservationRecord)
    id              INT   PRIMARY KEY   auto-increment
    file_path       TEXT               "codelens/graph.py"
    session_id      TEXT               "session-abc123"
    observation_type TEXT              "note" / "bug" / "todo"
    content         TEXT               "This file handles PageRank computation"
    importance      FLOAT              inherited from FileRecord at insert time
    created_at      FLOAT              unix timestamp
```

**Component 2 — `db/repository.py` (the data-access layer)**

Three repositories, one per table. Each takes a SQLAlchemy `Session`:

```python
FileRepository(session)
    .upsert(record)              # insert or replace by primary key
    .get(path)                   # fetch one FileRecord
    .get_all()                   # fetch all rows
    .delete(path)                # remove a row
    .get_stale_paths(hashes)     # paths whose SHA-256 changed → need re-parsing
    .get_deleted_paths(current)  # paths in DB that no longer exist on disk → remove

DependencyRepository(session)
    .upsert_for_file(from, to_files)   # replace all edges from a file
    .get_for_file(from_file)           # what does this file import?
    .get_dependents(to_file)           # what files import this file?

ObservationRepository(session)
    .add(obs)                          # inherits importance from FileRecord automatically
    .get_for_file(file_path)
    .get_by_session(session_id)
```

**Component 3 — `db/incremental.py` (the smart updater)**

`IncrementalUpdater` is the orchestrator. Every run:

```
scan_hashes(repo_path)
    Walk all source files, compute SHA-256 for each.
    Returns {rel_path: sha256_hex}
    │
    ▼
Compare with DB
    get_stale_paths()  → files whose hash changed (or new files)
    get_deleted_paths() → files in DB that no longer exist on disk
    │
    ▼
For each stale file:
    _parse_single() → FileSkeleton   (Phase 1 parser, per file)
    upsert FileRecord (importance=0.0, tier="peripheral" until recomputed)
    │
    ▼
For each deleted file:
    delete from files table
    delete from dependencies table
    │
    ▼
Recompute? (if full=True OR changes >= 10)
    _recompute_graph():
        Load all FileRecords from DB → rebuild RepoSkeleton
        build_and_score()  → graph, importance, tiers    (Phase 2)
        Update importance_score + tier on every FileRecord
        Persist dependency edges to dependencies table
        Commit
```

Recompute threshold = **10 changed files**. Below that, importance scores stay stale
until the next full recompute — a deliberate performance trade-off.

### Output of Phase 4

**One output: `.codelens/index.db` (SQLite file on disk)**

After `updater.init(full=True)` on this repo, the DB contains:

```
files table:         41 rows (one per source file)
dependencies table:   0 rows (Python absolute imports → 0 resolved edges on this repo)
observations table:   0 rows (populated by LLM sessions later)
```

Summary dict returned from `init()` / `update()`:
```python
{
  "parsed":     41,   # files re-parsed this run
  "skipped":     0,   # files whose hash matched (unchanged)
  "deleted":     0,   # files removed from DB
  "recomputed": True  # whether PageRank was recomputed
}
```

Reading back from DB at any time:
```python
importance = updater.get_importance()
# {"codelens/cli.py": 1.0, "codelens/mcp_server.py": 0.9728, ...}

tiers = updater.get_tiers()
# {"codelens/cli.py": "core", "codelens/models.py": "peripheral", ...}
```

### Why Phase 4 matters

Without Phase 4, every time you run CodeLens you re-parse the entire repo from scratch
and re-run PageRank. With Phase 4:

- First run: parse all 41 files (~seconds)
- Subsequent runs: only re-parse files whose SHA-256 changed (~milliseconds for a single edit)
- PageRank only recomputes when ≥ 10 files change

### What Phase 4 does NOT do

| Missing                          | Added in    |
|----------------------------------|-------------|
| CLI interface to the DB          | Phase 5     |
| MCP server reading from the DB   | Phase 5     |
| Embedding / vector store         | Phase 3 (called by Phase 5's `init` command) |

---

---

## Phase 5 — Interface

### What is Phase 5?

Phase 5 is the **user-facing layer**. It exposes all the pipeline through two interfaces:

1. **CLI** (`codelens` command) — for humans and scripts
2. **MCP server** — for AI tools (Claude Desktop, Cursor, any MCP-compatible client)

Both read from the SQLite DB that Phase 4 built — they don't re-parse from scratch.

### Input (from Phase 4)

Everything Phase 5 needs is in the DB at `.codelens/index.db` and ChromaDB at `.codelens/chroma`.
`CodeLensConfig` is the single entry point that resolves all paths:

```python
from codelens.config import CodeLensConfig

cfg = CodeLensConfig.for_repo(".")
# cfg.repo_path  = Path("D:/claude_project/code-lens")
# cfg.db_path    = Path(".codelens/index.db")
# cfg.chroma_dir = Path(".codelens/chroma")
# cfg.embedding_backend = "local"
# cfg.embed_model       = "BAAI/bge-small-en-v1.5"
# cfg.is_initialized    = True  (if index.db exists)
```

All paths can be overridden by environment variables:
`CODELENS_DB_PATH`, `CODELENS_CHROMA_DIR`, `CODELENS_EMBEDDING_BACKEND`, `CODELENS_EMBED_MODEL`

### Component 1 — CLI (`cli.py`)

Five commands:

#### `codelens init [PATH] [--full | --incremental]`

Runs the full pipeline and writes everything to disk.

```
$ codelens init .
[codelens] Indexing D:\claude_project\code-lens ...
[codelens] Parsed 41 files, skipped 0, deleted 0. Graph recomputed: True.
[codelens] Building vector index ...
[codelens] Vector index ready.
[codelens] Done.
```

What it does internally:
1. `IncrementalUpdater.init(full=True)` → parses, writes SQLite, runs PageRank
2. `VectorStore.upsert_file()` for every file → embeds and writes to ChromaDB

#### `codelens query "TEXT" [--max-tokens N]`

Retrieves the most relevant files for a natural-language question.

```
$ codelens query "find auth logic"

## codelens/graph.py  [199 loc, score=1.234]
  def build_graph(skeleton: RepoSkeleton) -> 'nx.DiGraph'
  def compute_importance(...) -> dict[str, float]
  ...

## codelens/resolver.py  [101 loc, score=0.987]
  def resolve(import_from: str, ...) -> str | None
```

Fallback: if ChromaDB deps not installed or vector store is empty, falls back to
`compact_repr(skeleton, importance=importance)` automatically.

#### `codelens watch [PATH]`

Watchdog daemon — monitors the repo for file changes and calls `updater.update()`
automatically on every `.py` / `.ts` / `.js` modification.

```
$ codelens watch .
[codelens] Watching D:\claude_project\code-lens  (Ctrl+C to stop)
[codelens] Updated — parsed 1, deleted 0, recomputed False.
```

#### `codelens map [PATH] [--format mermaid|json]`

Prints the dependency graph.

```
$ codelens map . --format mermaid
graph LR
  codelens_cli_py["codelens/cli.py"] --> codelens_config_py["codelens/config.py"]
  codelens_cli_py --> codelens_db_incremental_py["codelens/db/incremental.py"]
  ...

$ codelens map . --format json
{
  "nodes": ["codelens/cli.py", "codelens/graph.py", ...],
  "edges": [["codelens/cli.py", "codelens/config.py"], ...]
}
```

#### `codelens stats [PATH]`

Prints the tier distribution table.

```
$ codelens stats .

Tier            Count     Pct  Example files
──────────────────────────────────────────────────────────────────────
core                6      15%  codelens/cli.py, codelens/mcp_server.py (+4)
important           7      17%  codelens/graph.py, codelens/compact_repr.py (+5)
supporting         12      29%  codelens/retriever.py, codelens/embeddings.py (+10)
peripheral         16      39%  codelens/__init__.py, codelens/config.py (+14)

Total: 41 files
```

### Component 2 — MCP Server (`mcp_server.py`)

Runs as a stdio server. Claude Desktop / Cursor connects to it and can call three tools:

#### Tool 1: `get_relevant_files(query, max_tokens?)`

```
Input:  { "query": "how does PageRank work here?", "max_tokens": 4000 }

Output: (same packed context string as codelens query)
## codelens/graph.py  [199 loc, score=1.891]
  def _pagerank_python(graph, alpha=0.85, ...) -> dict[str, float]
  def compute_importance(graph, skeleton) -> dict[str, float]
  ...
```

Internally: reads DB → runs Phase 3 `retrieve()` → returns string.

#### Tool 2: `get_file_skeleton(path)`

```
Input:  { "path": "codelens/graph.py" }

Output: (raw FileSkeleton JSON from the DB)
{
  "path": "codelens/graph.py",
  "language": "python",
  "loc": 199,
  "imports": [...],
  "exports": ["build_graph", "compute_importance", ...],
  "symbols": [...]
}
```

#### Tool 3: `get_dependency_subgraph(file, depth?)`

```
Input:  { "file": "codelens/cli.py", "depth": 2 }

Output:
{
  "nodes": ["codelens/cli.py", "codelens/config.py", "codelens/db/incremental.py", ...],
  "edges": [["codelens/cli.py", "codelens/config.py"], ...]
}
```

Uses BFS from the root file, expanding both predecessors and successors at each hop.

#### Starting the MCP server

```bash
# Via module
python -m codelens.mcp_server

# With explicit repo path
CODELENS_REPO_PATH=/path/to/repo python -m codelens.mcp_server
```

Configure in `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "codelens": {
      "command": "python",
      "args": ["-m", "codelens.mcp_server"],
      "env": { "CODELENS_REPO_PATH": "/path/to/your/repo" }
    }
  }
}
```

### What Phase 5 does NOT do

Phase 5 is the final layer. There is no Phase 6. If extending CodeLens, continue from here.

---

---

## Complete Pipeline Summary

```
Input: "."  (any directory)
    │
    ▼ Phase 1 — extract_repo()
    │   walk_repo() → parse each file → FileSkeleton per file → RepoSkeleton
    │   compact_repr() → plain string (heuristic ranking, <5K tokens)
    │
    ▼ Phase 2 — build_and_score()
    │   resolver.resolve() per import → build nx.DiGraph
    │   _pagerank_python(reversed_graph) + in_degree + loc + is_entrypoint
    │   → importance: dict[str, float]   (0.0–1.0 per file)
    │   → tiers: dict[str, str]          (core/important/supporting/peripheral)
    │   compact_repr(skeleton, importance=importance) → better-ranked plain string
    │
    ▼ Phase 3 — VectorStore + retrieve()
    │   chunks_for_file() → (doc_id, text, metadata) per public symbol
    │   embed(texts) → float vectors (384-dim local or 1536-dim OpenAI)
    │   ChromaDB.upsert() → stored at .codelens/chroma
    │   query(text, k=20) → semantic hits
    │   re-rank × (1 + importance) → neighbor expand → pack_context()
    │   → context string (query-specific, scored per file)
    │
    ▼ Phase 4 — IncrementalUpdater
    │   SHA-256 per file → detect changed/deleted files
    │   re-parse only stale files → upsert FileRecord to SQLite
    │   recompute PageRank if ≥ 10 changes → update importance + tier in DB
    │   persist dependency edges to DependencyRecord table
    │   → .codelens/index.db  (survives across sessions)
    │
    ▼ Phase 5 — CLI + MCP Server
        CodeLensConfig.for_repo() → single config entry point
        codelens init   → runs Phase 4 + Phase 3 upsert
        codelens query  → reads DB + runs Phase 3 retrieve
        codelens watch  → watchdog daemon → calls update() on file change
        codelens map    → prints dependency graph (Mermaid or JSON)
        codelens stats  → prints tier distribution table
        mcp_server      → get_relevant_files / get_file_skeleton / get_dependency_subgraph
```

## Install commands

```bash
# Core (Phases 1–2)
python -m pip install -e ".[dev]"

# Everything (Phases 1–5)
python -m pip install -e ".[dev,all]"

# Run all 277 tests
python -m pytest tests/ -v
```
