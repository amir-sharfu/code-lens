from __future__ import annotations
import json
from pathlib import Path
from codelens.models import RepoSkeleton, FileSkeleton
from codelens.walker import walk_repo
from codelens.parsers import get_parser_for


def extract_repo(repo_path: str | Path) -> RepoSkeleton:
    """Walk an entire repository and extract file skeletons."""
    root = Path(repo_path).resolve()
    skeleton = RepoSkeleton(repo_path=str(root))
    languages_seen: set[str] = set()
    skipped = 0

    for abs_path, rel_posix in walk_repo(root):
        parser = get_parser_for(abs_path)
        if parser is None:
            skipped += 1
            continue

        try:
            source = abs_path.read_bytes()
        except OSError:
            skipped += 1
            continue

        file_skeleton = parser.parse(source, rel_posix)
        skeleton.files.append(file_skeleton)
        languages_seen.add(file_skeleton.language)

    skeleton.total_files = len(skeleton.files) + skipped
    skeleton.skipped_files = skipped
    skeleton.languages_found = sorted(languages_seen)
    return skeleton


def extract_file(file_path: str | Path, repo_root: str | Path) -> FileSkeleton | None:
    """Extract skeleton for a single file."""
    file_path = Path(file_path).resolve()
    repo_root = Path(repo_root).resolve()
    parser = get_parser_for(file_path)
    if parser is None:
        return None

    try:
        source = file_path.read_bytes()
        rel_posix = file_path.relative_to(repo_root).as_posix()
    except (OSError, ValueError):
        return None

    return parser.parse(source, rel_posix)


def to_json(skeleton: RepoSkeleton, indent: int = 2) -> str:
    """Serialize a RepoSkeleton to a JSON string using alias keys."""
    data = {
        "repo_path": skeleton.repo_path,
        "total_files": skeleton.total_files,
        "skipped_files": skeleton.skipped_files,
        "languages_found": skeleton.languages_found,
        "files": [f.to_dict() for f in skeleton.files],
    }
    return json.dumps(data, indent=indent, ensure_ascii=False)
