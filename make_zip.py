"""
make_zip.py

Packages the project into a distributable zip for demo delivery.
Run from the project root: python make_zip.py

Output: Competitive-Intelligence-Platform.zip (~100-150 MB compressed)
All files are nested under a top-level Competitive-Intelligence-Platform/ directory.
"""

import sys
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ZIP_NAME = "Competitive-Intelligence-Platform.zip"
TOP_DIR = "Competitive-Intelligence-Platform"

# Directory names that trigger exclusion (any path component matches)
EXCLUDE_DIR_NAMES = {
    ".venv",
    ".git",
    ".idea",
    ".claude",
    ".opencode",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
}

# Path prefixes (relative, POSIX) that trigger exclusion
EXCLUDE_PATH_PREFIXES = {
    "src/data",
    "docs/assignment",
}

# Exact filenames to exclude
EXCLUDE_FILENAMES = {
    ".env",
    ".coverage",
    "CLAUDE.md",
    "DEVELOPMENT-NOTES.md",
    "make_zip.py",
    "Dockerfile",
    "docker-compose.yml",
    "stop.bat",
    "install.bat",
    "install.sh",
}

# File extensions to exclude
EXCLUDE_EXTENSIONS = {".pyc", ".zip"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def should_exclude(rel: Path) -> bool:
    """Return True if the file at *rel* (relative to project root) should be skipped."""
    parts = rel.parts
    posix = rel.as_posix()

    # Any directory component matches excluded dir names
    for part in parts[:-1]:  # skip the filename itself
        if part in EXCLUDE_DIR_NAMES:
            return True

    # Path starts with an excluded prefix
    for prefix in EXCLUDE_PATH_PREFIXES:
        if posix == prefix or posix.startswith(prefix + "/"):
            return True

    # Filename matches
    if parts[-1] in EXCLUDE_FILENAMES:
        return True

    # Extension matches
    if rel.suffix in EXCLUDE_EXTENSIONS:
        return True

    return False


def human_size(n_bytes: int) -> str:
    """Format byte count as a human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} TB"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    project_root = Path(__file__).resolve().parent
    zip_path = project_root / ZIP_NAME

    # Collect files first so we can show a total count
    all_files = sorted(
        f for f in project_root.rglob("*") if f.is_file()
    )
    to_include = [f for f in all_files if not should_exclude(f.relative_to(project_root))]

    print(f"Creating {ZIP_NAME} ...")
    print(f"  {len(to_include)} files to include (out of {len(all_files)} found)")
    print()

    db_path = project_root / "src" / "db.sqlite3"
    added = 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for file in to_include:
            rel = file.relative_to(project_root)

            # Call out the large database so the user knows why it pauses
            if file == db_path:
                print(f"  Adding src/db.sqlite3 ({human_size(file.stat().st_size)}) — this may take a minute...")
                sys.stdout.flush()

            arcname = f"{TOP_DIR}/{rel.as_posix()}"
            zf.write(file, arcname)
            added += 1

    final_size = zip_path.stat().st_size
    print()
    print(f"Done! {ZIP_NAME} — {human_size(final_size)}")
    print(f"  ({added} files archived)")


if __name__ == "__main__":
    main()
