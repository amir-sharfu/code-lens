"""
Compact architectural representation of a RepoSkeleton.

Produces a human-readable, token-efficient summary suitable for LLM context.
Target: <5K tokens for most repos up to ~500 files.
"""
from __future__ import annotations
from pathlib import Path
from codelens.models import RepoSkeleton, FileSkeleton, SymbolEntry


def _short_doc(doc: str | None, max_len: int = 80) -> str:
    if not doc:
        return ""
    first_line = doc.strip().splitlines()[0].strip()
    if len(first_line) > max_len:
        first_line = first_line[:max_len - 1] + "…"
    return f'  "{first_line}"'


def _short_sig(sig: str, max_len: int = 100) -> str:
    sig = sig.strip()
    if len(sig) > max_len:
        sig = sig[:max_len - 1] + "…"
    return sig


_TEST_INDICATORS = frozenset({"test", "tests", "spec", "specs", "fixtures", "conftest"})
_EXAMPLE_INDICATORS = frozenset({"example", "examples", "demo", "demos", "sample"})
_CORE_NAMES = frozenset({
    "core", "main", "app", "server", "cli", "api", "client",
    "index", "base", "manager", "router", "handler",
})


def _score_file(f: FileSkeleton) -> float:
    """Heuristic importance proxy used when Phase 2 importance scores are not available."""
    parts = set(f.path.lower().replace("\\", "/").split("/"))
    stem = Path(f.path).stem

    # Heavily penalise test and example files
    if parts & _TEST_INDICATORS or stem.startswith("test_") or stem.endswith("_test"):
        return 0.0
    if parts & _EXAMPLE_INDICATORS:
        return 0.05

    public_syms = sum(1 for s in f.symbols if not s.name.startswith("_"))
    export_count = len(f.exports)
    class_count = sum(1 for s in f.symbols if s.kind == "class")
    is_init = stem in ("__init__", "index")
    is_core = stem.lower() in _CORE_NAMES

    # Bonus for src/ layout
    src_bonus = 0.1 if "src" in parts else 0.0

    return (
        0.35 * min(public_syms, 30) / 30
        + 0.25 * min(export_count, 20) / 20
        + 0.15 * min(class_count, 10) / 10
        + 0.1 * (1 if is_init else 0)
        + 0.05 * (1 if is_core else 0)
        + src_bonus
    )


def _format_file_block(f: FileSkeleton, max_symbols: int = 12) -> str:
    lines: list[str] = []

    # Header: path + loc
    exports_hint = ""
    if f.exports and f.exports != ["*"]:
        shown = f.exports[:8]
        rest = len(f.exports) - len(shown)
        exports_hint = ", ".join(shown)
        if rest > 0:
            exports_hint += f" (+{rest})"
        exports_hint = f"  -> {exports_hint}"
    lines.append(f"## {f.path}  [{f.loc} loc]{exports_hint}")

    # Public symbols only; classes before functions before variables/types
    public = [s for s in f.symbols if not s.name.startswith("_")]
    order = {"class": 0, "function": 1, "method": 2, "type": 3, "variable": 4}
    public.sort(key=lambda s: order.get(s.kind, 9))

    shown = public[:max_symbols]
    for sym in shown:
        prefix = "  "
        sig = _short_sig(sym.signature)
        doc = _short_doc(sym.doc)
        async_tag = "async " if sym.is_async and "async" not in sig else ""
        line = f"{prefix}{async_tag}{sig}"
        if doc:
            line += f"\n    {doc}"
        lines.append(line)

    if len(public) > max_symbols:
        lines.append(f"  … +{len(public) - max_symbols} more symbols")

    return "\n".join(lines)


def compact_repr(
    skeleton: RepoSkeleton,
    max_files: int = 40,
    max_symbols_per_file: int = 10,
    token_budget: int = 5000,
    importance: dict[str, float] | None = None,
) -> str:
    """
    Produce a compact architectural text representation of a repo skeleton.

    Args:
        skeleton:             The repo skeleton from extract_repo().
        max_files:            Hard cap on files shown.
        max_symbols_per_file: Max symbols shown per file.
        token_budget:         Approximate token limit (chars / 4).
        importance:           Optional dict[path, score] from Phase 2 graph
                              analysis. When provided, replaces _score_file().

    Returns:
        A text string suitable for LLM context.
    """
    char_budget = token_budget * 4

    def _rank_key(f: FileSkeleton) -> float:
        if importance is not None:
            return importance.get(f.path, 0.0)
        return _score_file(f)

    ranked = sorted(skeleton.files, key=_rank_key, reverse=True)

    # Language breakdown
    lang_counts: dict[str, int] = {}
    for f in skeleton.files:
        lang_counts[f.language] = lang_counts.get(f.language, 0) + 1
    lang_summary = ", ".join(
        f"{lang} ({count} {'file' if count == 1 else 'files'})"
        for lang, count in sorted(lang_counts.items(), key=lambda x: -x[1])
    )

    total_loc = sum(f.loc for f in skeleton.files)
    total_symbols = sum(
        len([s for s in f.symbols if not s.name.startswith("_")])
        for f in skeleton.files
    )

    header = (
        f"# Repository Architecture\n"
        f"# {Path(skeleton.repo_path).name}\n"
        f"# {skeleton.total_files} source files · {lang_summary} · {total_loc:,} total LOC\n"
        f"# {total_symbols} public symbols\n"
    )

    # Entrypoints section
    entrypoints = [f for f in skeleton.files if f.is_entrypoint]
    if entrypoints:
        ep_list = "  " + "\n  ".join(f.path for f in entrypoints[:10])
        header += f"\n## Entry Points\n{ep_list}\n"

    blocks: list[str] = []
    char_used = len(header)
    files_shown = 0

    for f in ranked:
        if files_shown >= max_files:
            break
        # Skip files with no useful public content
        public_count = sum(1 for s in f.symbols if not s.name.startswith("_"))
        if public_count == 0 and not f.exports:
            continue

        block = _format_file_block(f, max_symbols=max_symbols_per_file)
        block_chars = len(block) + 2  # +2 for newlines between blocks

        if char_used + block_chars > char_budget:
            # Try with fewer symbols
            block = _format_file_block(f, max_symbols=3)
            block_chars = len(block) + 2
            if char_used + block_chars > char_budget:
                remaining = len(ranked) - files_shown
                blocks.append(f"# … {remaining} more files omitted (budget reached)")
                break

        blocks.append(block)
        char_used += block_chars
        files_shown += 1

    output = header + "\n" + "\n\n".join(blocks)

    # Final token count
    approx_tokens = len(output) // 4
    output += f"\n\n# --- end of architectural summary ({approx_tokens:,} approx tokens) ---"
    return output
