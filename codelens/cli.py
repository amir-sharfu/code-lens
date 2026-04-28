"""
CodeLens CLI — five commands backed by the full Phase 1-4 pipeline.

  codelens init  [PATH] [--full]          scan → DB + optional vector index
  codelens query TEXT   [--max-tokens N]  hybrid retrieval, print context
  codelens watch [PATH]                   watchdog daemon, incremental updates
  codelens map   [PATH] [--format …]      dependency graph as mermaid or JSON
  codelens stats [PATH]                   tier distribution table
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
from typing import Optional

try:
    import typer
except ImportError as exc:
    raise ImportError("typer is required for the CLI: pip install typer>=0.12") from exc

from codelens.config import CodeLensConfig
from codelens.db.incremental import IncrementalUpdater
from codelens.db.repository import FileRepository, DependencyRepository
from codelens.db.schema import get_engine, create_tables

app = typer.Typer(
    name="codelens",
    help="Compressed architectural orientation for LLMs.",
    add_completion=False,
)

_PATH_ARG = typer.Argument(".", help="Repository root (defaults to current directory).")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_init(cfg: CodeLensConfig) -> None:
    if not cfg.is_initialized:
        typer.echo(
            f"[codelens] Not initialised. Run: codelens init {cfg.repo_path}",
            err=True,
        )
        raise typer.Exit(code=1)


def _open_session(cfg: CodeLensConfig):
    from sqlalchemy.orm import Session as _Session
    engine = get_engine(cfg.db_path)
    return _Session(engine)


def _graph_from_db(session) -> "nx.DiGraph":
    """Reconstruct a DiGraph from persisted dependency rows."""
    import networkx as nx
    from codelens.db.schema import DependencyRecord

    g: nx.DiGraph = nx.DiGraph()
    for rec in FileRepository(session).get_all():
        g.add_node(rec.path, tier=rec.tier, importance=rec.importance_score)
    for dep in session.query(DependencyRecord).all():
        g.add_edge(dep.from_file, dep.to_file)
    return g


def _skeleton_from_db(session, repo_path: Path):
    """Reconstruct a RepoSkeleton from persisted file rows."""
    from codelens.models import RepoSkeleton, FileSkeleton

    records = FileRepository(session).get_all()
    files = []
    for rec in records:
        try:
            files.append(FileSkeleton.model_validate(json.loads(rec.skeleton_json)))
        except Exception:
            continue
    return RepoSkeleton(
        repo_path=str(repo_path),
        files=files,
        total_files=len(files),
    )


def _try_populate_vector_store(cfg: CodeLensConfig, session) -> bool:
    """Embed all indexed files into ChromaDB. Returns True on success."""
    try:
        from codelens.vector_store import VectorStore
        from codelens.embeddings import get_embedding_backend
        backend = get_embedding_backend(cfg.embedding_backend)
        vs = VectorStore(persist_dir=cfg.chroma_dir, backend=backend)
        for rec in FileRepository(session).get_all():
            from codelens.models import FileSkeleton
            try:
                f = FileSkeleton.model_validate(json.loads(rec.skeleton_json))
                vs.upsert_file(f)
            except Exception:
                continue
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@app.command()
def init(
    path: Path = _PATH_ARG,
    full: bool = typer.Option(True, "--full/--incremental", help="Full re-parse vs incremental."),
) -> None:
    """Scan the repository, build the dependency graph, and populate the index."""
    cfg = CodeLensConfig.for_repo(path)
    typer.echo(f"[codelens] Indexing {cfg.repo_path} …")

    updater = IncrementalUpdater(cfg.repo_path, db_path=cfg.db_path)
    summary = updater.init(full=full)

    typer.echo(
        f"[codelens] Parsed {summary['parsed']} files, "
        f"skipped {summary['skipped']}, "
        f"deleted {summary['deleted']}. "
        f"Graph recomputed: {summary['recomputed']}."
    )

    typer.echo("[codelens] Building vector index …")
    with _open_session(cfg) as session:
        ok = _try_populate_vector_store(cfg, session)
    if ok:
        typer.echo("[codelens] Vector index ready.")
    else:
        typer.echo(
            "[codelens] Vector index skipped "
            "(install phase3 deps: pip install codelens[phase3])."
        )

    typer.echo("[codelens] Done.")


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------

@app.command()
def query(
    text: str = typer.Argument(..., help="Natural-language query."),
    path: Path = typer.Option(".", "--path", "-p", help="Repository root."),
    max_tokens: int = typer.Option(4000, "--max-tokens", "-t"),
) -> None:
    """Retrieve the most relevant files for a query and print the context."""
    cfg = CodeLensConfig.for_repo(path)
    _require_init(cfg)

    with _open_session(cfg) as session:
        importance = {r.path: r.importance_score for r in FileRepository(session).get_all()}
        skeleton = _skeleton_from_db(session, cfg.repo_path)
        graph = _graph_from_db(session)

    # Try semantic retrieval; fall back to structural compact_repr
    try:
        from codelens.vector_store import VectorStore
        from codelens.embeddings import get_embedding_backend
        from codelens.retriever import retrieve

        backend = get_embedding_backend(cfg.embedding_backend)
        vs = VectorStore(persist_dir=cfg.chroma_dir, backend=backend)
        if vs.count == 0:
            raise RuntimeError("Vector store is empty — run `codelens init` first.")
        context = retrieve(text, vs, graph, importance, skeleton, max_tokens=max_tokens)
    except (ImportError, RuntimeError):
        from codelens.compact_repr import compact_repr
        context = compact_repr(skeleton, importance=importance, token_budget=max_tokens)

    typer.echo(context)


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------

@app.command()
def watch(path: Path = _PATH_ARG) -> None:
    """Watch for file changes and incrementally update the index."""
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError as exc:
        typer.echo(
            "[codelens] watchdog is required: pip install watchdog>=4.0", err=True
        )
        raise typer.Exit(code=1) from exc

    cfg = CodeLensConfig.for_repo(path)
    _require_init(cfg)

    updater = IncrementalUpdater(cfg.repo_path, db_path=cfg.db_path)

    _SOURCE_EXTS = frozenset({".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})

    class _Handler(FileSystemEventHandler):
        def on_modified(self, event):
            if not event.is_directory and Path(event.src_path).suffix.lower() in _SOURCE_EXTS:
                s = updater.update()
                typer.echo(
                    f"[codelens] Updated — parsed {s['parsed']}, "
                    f"deleted {s['deleted']}, recomputed {s['recomputed']}."
                )

    observer = Observer()
    observer.schedule(_Handler(), str(cfg.repo_path), recursive=True)
    observer.start()
    typer.echo(f"[codelens] Watching {cfg.repo_path}  (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()


# ---------------------------------------------------------------------------
# map
# ---------------------------------------------------------------------------

@app.command(name="map")
def map_cmd(
    path: Path = _PATH_ARG,
    format: str = typer.Option("mermaid", "--format", "-f", help="mermaid or json"),
) -> None:
    """Print the dependency graph as Mermaid or JSON."""
    cfg = CodeLensConfig.for_repo(path)
    _require_init(cfg)

    with _open_session(cfg) as session:
        from codelens.db.schema import DependencyRecord
        nodes = [r.path for r in FileRepository(session).get_all()]
        edges = [(d.from_file, d.to_file) for d in session.query(DependencyRecord).all()]

    if format == "json":
        typer.echo(json.dumps({"nodes": nodes, "edges": edges}, indent=2))
    else:
        lines = ["graph LR"]
        seen: set[tuple[str, str]] = set()
        for src, dst in edges:
            if (src, dst) not in seen:
                # Sanitise node IDs for Mermaid (replace slashes and dots)
                s = src.replace("/", "_").replace(".", "_")
                d = dst.replace("/", "_").replace(".", "_")
                lines.append(f'  {s}["{src}"] --> {d}["{dst}"]')
                seen.add((src, dst))
        typer.echo("\n".join(lines))


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

@app.command()
def stats(path: Path = _PATH_ARG) -> None:
    """Print a tier-distribution table for all indexed files."""
    cfg = CodeLensConfig.for_repo(path)
    _require_init(cfg)

    with _open_session(cfg) as session:
        records = FileRepository(session).get_all()

    if not records:
        typer.echo("[codelens] No files indexed yet. Run `codelens init` first.")
        return

    tier_order = ["core", "important", "supporting", "peripheral"]
    buckets: dict[str, list[str]] = {t: [] for t in tier_order}
    for rec in records:
        tier = rec.tier if rec.tier in buckets else "peripheral"
        buckets[tier].append(rec.path)

    total = len(records)
    typer.echo(f"\n{'Tier':<14} {'Count':>6}  {'Pct':>6}  Example files")
    typer.echo("─" * 70)
    for tier in tier_order:
        paths = buckets[tier]
        count = len(paths)
        pct = f"{count / total * 100:.0f}%" if total else "0%"
        examples = ", ".join(paths[:3])
        if len(paths) > 3:
            examples += f" (+{len(paths) - 3})"
        typer.echo(f"{tier:<14} {count:>6}  {pct:>6}  {examples}")
    typer.echo(f"\nTotal: {total} files")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app()


if __name__ == "__main__":
    main()
