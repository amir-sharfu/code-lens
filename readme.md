# Building CodeLens: A Deep Technical Roadmap

Let me give you a real builder's blueprint. I'll structure this so you can start coding tomorrow.

---

## Phase 1: The Skeleton Stripper (Part-1)

This is your foundation. The goal: take any repository and produce a compressed architectural representation an LLM can reason about in under 5k tokens.

### The Tech Stack

**Core parser: tree-sitter**
- Why: It's the same parser GitHub, Neovim, and Cursor use. Fast, incremental, supports 40+ languages.
- Install: `pip install tree-sitter tree-sitter-languages`
- Alternative for Python-only: Python's built-in `ast` module is simpler if you're prototyping.

**Why not regex?** People try this first and regret it. A regex can't tell the difference between `function foo()` in a comment versus actual code. ASTs give you semantic truth.

### What "Skeleton" Actually Means

You're extracting four things per file:

1. **Imports/dependencies** — what this file *uses*
2. **Exports/public API** — what this file *provides*
3. **Class and function signatures** — names, parameters, return types (no bodies)
4. **Docstrings/leading comments** — the human intent, if present

Here's the data structure I'd target:

```json
{
  "path": "src/auth/login.ts",
  "language": "typescript",
  "imports": [
    { "from": "./db", "symbols": ["UserModel"] },
    { "from": "bcrypt", "symbols": ["compare"] }
  ],
  "exports": ["loginUser", "validateSession"],
  "symbols": [
    {
      "kind": "function",
      "name": "loginUser",
      "signature": "async (email: string, password: string) => Promise<Session>",
      "doc": "Authenticates a user and returns a session token",
      "line": 12
    }
  ],
  "loc": 87
}
```

### Building the Parser

Pseudocode for the core loop:

```python
def extract_skeleton(repo_path):
    skeleton = {}
    for file in walk_repo(repo_path):
        if not is_source_file(file):
            continue
        tree = parser.parse(file.read_bytes())
        skeleton[file.relative_path] = {
            "imports": extract_imports(tree),
            "exports": extract_exports(tree),
            "symbols": extract_signatures(tree),
            "loc": count_lines(file)
        }
    return skeleton
```

### The Gotchas Nobody Warns You About

- **Monorepos**: Respect `.gitignore` AND `node_modules`, `venv`, `dist`, `build`, `.next`. Use the `pathspec` library.
- **Generated code**: Files like `*.pb.go`, `schema.graphql.ts` will pollute your skeleton. Add a heuristic: skip files over ~2000 lines or with `// AUTO-GENERATED` headers.
- **Dynamic imports**: `require(variable)` or `import(path)` won't show up in static AST. Document this as a known limitation.
- **Re-exports**: `export * from './foo'` is common. You need to follow these or your dependency graph will be wrong.

---

## Phase 2: The Dependency Graph (Part 2)

Now you turn that skeleton into a graph where importance can be measured.

### The Graph Model

Use **NetworkX** (Python) or **graphology** (JS). Don't build this from scratch.

```python
import networkx as nx

graph = nx.DiGraph()
for file_path, data in skeleton.items():
    graph.add_node(file_path, **data)
    for imp in data["imports"]:
        resolved = resolve_import_path(imp["from"], file_path)
        if resolved:
            graph.add_edge(file_path, resolved)
```

### Importance Scoring: The Real Math

This is where most projects get lazy. Don't just count imports. Use **PageRank**.

```python
importance = nx.pagerank(graph, alpha=0.85)
```

PageRank gives high scores to files that are imported by *other important files*. So your `database.ts` that's imported by 5 critical services scores higher than a `utils.ts` imported by 20 leaf components.

Combine it with a few other signals:

```python
def compute_importance(graph, file):
    return (
        0.5 * pagerank[file]              # structural centrality
        + 0.2 * normalize(in_degree[file])  # how many files depend on it
        + 0.2 * normalize(loc[file])        # size as a weak proxy
        + 0.1 * is_entrypoint(file)         # main.ts, index.js, app.py bonus
    )
```

### Detecting Entrypoints

Look for:
- `main.py`, `index.ts`, `app.tsx`, `server.js` at root or `src/`
- Files referenced in `package.json` `"main"` field
- Files with `if __name__ == "__main__"` (Python)
- Files containing top-level Express/FastAPI/Flask app declarations

Entrypoints are your "kings" — they should always rank high regardless of PageRank.

### Labeling Tiers

Once you have scores, bucket them:

```python
def assign_tier(score, percentile):
    if percentile >= 0.9: return "Core"           # top 10%
    elif percentile >= 0.7: return "Important"    # top 30%
    elif percentile >= 0.4: return "Supporting"
    else: return "Peripheral"
```

This is your "label" system, but earned through math, not guessed.

---

## Phase 3: Smart Retrieval (Part 3)

This is what makes it actually useful for an LLM.

### The Hybrid Retrieval Strategy

When a user asks *"how does login work?"*, you want to assemble context that includes:

1. **Semantically relevant files** (vector search)
2. **Structurally important files** (graph centrality)
3. **Their immediate neighbors** (graph traversal)

Use a vector store for the semantic layer. **sqlite-vec** or **ChromaDB** are perfect for local-first tools — no separate server needed.

### Embedding What

Don't embed entire files. Embed:
- The skeleton signatures (one chunk per function/class)
- File-level summaries (auto-generate with a small LLM call)
- Folder-level READMEs if they exist

For embeddings, **`text-embedding-3-small`** (OpenAI) or **`bge-small-en`** (local, free, runs on CPU) are both solid.

### The Retrieval Algorithm

```python
def get_context(query, max_tokens=4000):
    # 1. Semantic recall — find what the query is about
    candidates = vector_store.search(query, k=20)
    
    # 2. Boost by structural importance
    for c in candidates:
        c.score *= (1 + importance_scores[c.file])
    
    # 3. Expand by 1-hop graph neighbors
    expanded = set()
    for c in candidates[:5]:
        expanded.add(c.file)
        expanded.update(graph.successors(c.file))   # what it imports
        expanded.update(graph.predecessors(c.file)) # what imports it
    
    # 4. Pack into token budget, important first
    return pack_context(expanded, max_tokens)
```

### Token Packing

Always order context by importance descending. This directly fights the "Lost in the Middle" problem — the LLM weights the start and end of context most, so put your `Core` files at the top, `Peripheral` files at the bottom.

---

## Phase 4: The Persistence Layer (Part 4)

This is where you out-engineer `claude-mem`.

### Schema (SQLite)

```sql
CREATE TABLE files (
    path TEXT PRIMARY KEY,
    skeleton_json TEXT,
    importance_score REAL,
    tier TEXT,                    -- Core | Important | Supporting | Peripheral
    last_modified TIMESTAMP,
    content_hash TEXT             -- for incremental updates
);

CREATE TABLE dependencies (
    from_file TEXT,
    to_file TEXT,
    import_type TEXT,             -- 'static' | 'dynamic' | 're-export'
    PRIMARY KEY (from_file, to_file)
);

CREATE TABLE observations (
    id INTEGER PRIMARY KEY,
    file_path TEXT,
    session_id TEXT,
    observation_type TEXT,        -- discovery | decision | bug | refactor
    content TEXT,
    embedding BLOB,
    importance INHERITED REAL,    -- inherits from file's importance
    created_at TIMESTAMP
);
```

The key innovation over `claude-mem`: **observations inherit weight from the file they're about**. A discovery in a Core file matters more than one in a Peripheral file.

### Incremental Updates

Don't reparse the whole repo every time. Use file content hashes:

```python
def update_incremental(repo_path):
    for file in walk_repo(repo_path):
        new_hash = sha256(file.read_bytes())
        if db.get_hash(file.path) != new_hash:
            reparse_and_update(file)
            mark_dependents_for_recompute(file)
```

When a file changes, only its skeleton needs reparsing — but the importance scores of its dependents may shift, so recompute PageRank on a schedule (e.g., every 10 file changes or on commit).

---

## Phase 5: The Interface (Part 5)

### CLI First

```bash
codelens init           # scan repo, build initial skeleton
codelens query "how does auth work?"  # returns ranked context
codelens watch          # daemon that updates on file changes
codelens map            # output Mermaid diagram of top-tier files
codelens stats          # show tier distribution, top 10 files
```

### MCP Server (the real unlock)

Expose CodeLens as a Model Context Protocol server. This is what makes it plug-and-play with Claude Desktop, Cursor, and other modern AI tools.

The MCP server exposes tools like:
- `get_relevant_files(query)` 
- `get_file_skeleton(path)`
- `get_dependency_subgraph(file, depth)`

This is the integration layer that turns your tool from a script into infrastructure.

---

## What to Skip (For Now)

- **Don't** build a web UI in v1. CLI + MCP is enough to prove value.
- **Don't** support every language. Start with TypeScript/JavaScript and Python — that's 80% of modern codebases.
- **Don't** try to do real-time collaboration or multi-user state. Local-first.
- **Don't** generate visual diagrams as your core feature. Mermaid output as a `--format=diagram` flag is fine; building a visual editor is a year-long detour.

---

## Realistic Timeline

| Week | Milestone |
|------|-----------|
| 1-2  | Skeleton stripper for TS + Python, JSON output |
| 2-3  | Dependency graph + PageRank + tier assignment |
| 3-4  | Vector store integration + hybrid retrieval |
| 4    | SQLite persistence + incremental updates |
| 5    | CLI polish + MCP server |
| 6    | Documentation + first public release |

Six weeks to a real, useful, open-source tool. Solo and part-time, double it — three months to v1.

---

## The One Thing That Will Make or Break This

**Benchmark it honestly from day one.** Pick 3 real open-source repos (something like Next.js, FastAPI, and a smaller project). Write 10 questions about each ("where is rate limiting handled?", "how does middleware chaining work?"). Measure: does CodeLens-assembled context let an LLM answer correctly more often than naive file-dump or pure vector search?

If the answer is yes, you have a project people will use. If it's no, iterate on the retrieval algorithm before writing more code. Most RAG-for-code tools fail because the authors never measured retrieval quality — they just shipped vibes.

Want me to go deeper on any specific phase — the AST parsing, the PageRank tuning, the MCP server design, or the evaluation harness?

--------------------------------------------
## Problem
# The Real Problem, Plain and Simple

Let me strip this down to what actually matters.

---

## The Problem in One Sentence

**When you ask an AI to help with a big codebase, it doesn't know which files are important — so it either reads everything (slow, expensive, confused) or guesses wrong.**

That's it. That's the whole problem.

---

## A Simple Analogy

Imagine you hire a new engineer on Monday. You hand them a USB stick with 500 files and say *"fix the login bug."*

They have two bad options:

1. **Read every file** — takes a week, they're exhausted, they still might miss the point
2. **Grep for "login"** — finds 47 matches, half are comments, they fix the wrong one

What they actually need is for *you* to walk in and say:

> *"Here's how the app is structured. These three files are the heart of authentication. Everything else is supporting cast. Start there."*

**That five-minute orientation is worth more than a week of solo reading.**

LLMs today don't get that orientation. Every conversation starts like that confused new hire on Monday.

---

## Why Current Tools Fall Short

**Vector search** (what most tools do today) is like searching Google for "login." You get pages that *mention* the word, but not necessarily the page that *matters most*. A throwaway comment and the core auth function look equally relevant.

**Flat memory tools** (like `claude-mem`) remember facts but treat them all as equal weight. *"The server runs on port 3000"* sits next to *"This is the central authentication module"* — same importance, no hierarchy.

Both miss the same thing: **architectural awareness**. They know *what's there*. They don't know *what matters*.

---

## The Fix in Three Pieces

**1. Strip the codebase to its bones.**
Remove the implementation details. Keep only the structure — file names, function names, what imports what. A 100,000-line repo becomes a 2-page outline. The AI can hold that whole outline in its head at once.

**2. Score every file by importance.**
Use a simple math trick borrowed from how Google ranks web pages: a file is important if other important files depend on it. Your `database.ts` that everything connects to gets a high score. The little `formatDate.ts` helper gets a low score. Now the AI knows the difference between the foundation and the decoration.

**3. Hand the AI the right files at the right time.**
When the user asks a question, don't dump everything. Find the relevant files (by meaning) AND the structurally important files (by score), and feed those in first. Important stuff at the top of context, supporting stuff below.

---

## Why This Works

LLMs aren't dumb. They're just **flying blind in your codebase**. Give them a map (structure) and a guide (importance scores), and they perform dramatically better — without any change to the model itself.

You're not making the AI smarter. You're making it **less confused**.

---

## What You're Actually Building

A tool that does one thing: **takes a messy codebase and gives the AI a proper orientation before it starts working.**

That's the whole pitch. Everything else — the graphs, the PageRank, the MCP server — is just engineering to make that one thing work reliably.

---

Does this version land better? If yes, I'd say go build the skeleton stripper first. It's the smallest piece that proves the whole idea.


------------------------
Github link : https://github.com/VoltAgent/awesome-claude-code-subagents.git
Target Plugin: voltagent-data-ai