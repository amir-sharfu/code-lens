# CodeLens

> Extract compressed architectural skeletons from codebases so LLMs get structural orientation before working in unfamiliar code.

LLMs either read everything (slow, expensive) or grep blindly (misses structure). CodeLens solves this by reducing any repo to a **<5K token outline** that ranks every file by importance — so the AI knows the difference between the foundation and the decoration before writing a single line.

---

## How It Works

```
walk_repo()  →  parser.parse()  →  FileSkeleton  →  RepoSkeleton
  →  build_graph()  →  compute_importance()  →  assign_tiers()
  →  compact_repr(skeleton, importance=scores)  →  str (<5K tokens)
  →  VectorStore.upsert_file()  →  retrieve(query, ...)  →  packed context
  →  IncrementalUpdater  →  SQLite DB (.codelens/index.db)
  →  CLI / MCP server
```

**Phase 1** — Multi-language parser (Python, TypeScript, JavaScript) → `FileSkeleton` → `RepoSkeleton`  
**Phase 2** — Dependency graph with pure-Python PageRank → importance scores → tier labels  
**Phase 3** — ChromaDB vector store + hybrid retrieval (semantic + structural re-rank)  
**Phase 4** — SQLite persistence with SHA-256 incremental updates  
**Phase 5** — Typer CLI + MCP stdio server  

---

## Quick Start

```bash
pip install codelens[all]

# Index a repo
codelens init /path/to/repo

# Query it
codelens query "how does authentication work?"

# View dependency map
codelens map /path/to/repo --format mermaid

# Tier distribution
codelens stats /path/to/repo
```

### MCP Server (Claude Desktop / Cursor)

```bash
export CODELENS_REPO_PATH=/path/to/repo
python -m codelens.mcp_server
```

Exposes three tools: `get_relevant_files`, `get_file_skeleton`, `get_dependency_subgraph`.

---

## Installation

```bash
# Core only (Phase 1 + 2)
pip install codelens

# With vector search (Phase 3)
pip install codelens[phase3]

# With persistence (Phase 4)
pip install codelens[phase4]

# With CLI + MCP server (Phase 5)
pip install codelens[phase5]

# Everything
pip install codelens[all]
```

**Python 3.11+ required.**

---

## Importance Scoring

Every file gets a composite score:

```
score = 0.5 × pagerank × N   +  0.2 × in_degree_norm
      + 0.2 × loc_norm        +  0.1 × is_entrypoint
```

PageRank runs on the **reversed** import graph — files that many important files depend on score highest. Scores are bucketed into four tiers:

| Tier | Percentile | Meaning |
|------|-----------|---------|
| `core` | ≥ 90th | Foundation files — always include |
| `important` | ≥ 70th | Key modules |
| `supporting` | ≥ 40th | Utility and helper code |
| `peripheral` | < 40th | Leaf files, rarely needed |

---

## Python API

```python
# Phase 1 — extract skeleton
from codelens import extract_repo, build_and_score
from codelens.compact_repr import compact_repr

skeleton = extract_repo(".")
graph, importance, tiers = build_and_score(skeleton)
print(compact_repr(skeleton, importance=importance))  # <5K tokens

# Phase 3 — semantic retrieval
from codelens import VectorStore, retrieve, get_embedding_backend

backend = get_embedding_backend()          # local bge-small or OpenAI
vs = VectorStore(backend, repo_path=".")
context = retrieve("auth logic", vs, skeleton, importance, graph)

# Phase 4 — incremental indexing
from codelens import IncrementalUpdater

updater = IncrementalUpdater(repo_path=".")
updater.update()                           # only re-parses changed files
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CODELENS_EMBEDDING_BACKEND` | `local` | `local` or `openai` |
| `CODELENS_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | Override local model |
| `OPENAI_API_KEY` | — | Required if backend=`openai` |
| `CODELENS_REPO_PATH` | `cwd` | Repo path for MCP server |

---

## Adding a Language Parser

1. Create `codelens/parsers/<lang>_parser.py` subclassing `BaseParser`
2. Set `LANGUAGE`, `EXTENSIONS`, implement `parse(source: bytes, rel_path: str) -> FileSkeleton`
3. Register in `codelens/parsers/__init__.py` `_REGISTRY`
4. Add fixture to `tests/fixtures/` and test file `tests/test_<lang>_parser.py`

---

## Development

```bash
git clone https://github.com/amir-sharfu/code-lens.git
cd code-lens
pip install -e ".[dev,all]"

# Run tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=codelens --cov-report=term-missing
```

**277 tests, all passing.**

---

## License

MIT
