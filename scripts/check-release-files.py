#!/usr/bin/env python3
"""Read-only privacy preflight for the publication allowlist. Does not use Git."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL = ("README.md", ".gitignore", "Package.swift")
SOURCE_DIRS = ("Sources", "Resources", "Artwork", "scripts", "tests")
TEXT_SUFFIXES = {".swift", ".py", ".js", ".sh", ".md", ".plist", ".entitlements"}
ARTWORK = {"Artwork/FreeParkingIcon.png", "Artwork/FreeParkingBanner.png", "Resources/AppIcon.icns"}
PATTERNS = (
    ("private home path", re.compile(r"/Users/(?!example/|Shared/)[A-Za-z0-9._-]+/")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("access token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{24,}|sk-[A-Za-z0-9_-]{30,})\b")),
)


def publication_files():
    paths = [ROOT / name for name in TOP_LEVEL]
    for name in SOURCE_DIRS:
        paths.extend(p for p in (ROOT / name).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store")
    return sorted(paths)


def main():
    private_terms_file = ROOT / ".local/private-terms.txt"
    private_terms = (private_terms_file.read_text().splitlines() if private_terms_file.exists() else [])
    problems = []
    paths = publication_files()
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        if path.is_symlink():
            problems.append((relative, "symlink requires manual review"))
            continue
        if not path.is_file():
            problems.append((relative, "required file missing"))
            continue
        if relative in ARTWORK:
            # Only these generated icon assets are allowed to be binary.
            continue
        if path.suffix not in TEXT_SUFFIXES and relative != ".gitignore":
            problems.append((relative, "unexpected file type"))
            continue
        try:
            text = path.read_text()
        except UnicodeError:
            problems.append((relative, "unexpected binary file"))
            continue
        for description, pattern in PATTERNS:
            if pattern.search(text):
                problems.append((relative, description))
        if any(term.strip() and term.casefold().strip() in text.casefold() for term in private_terms):
            problems.append((relative, "private term from local exclusion list"))
    for path, issue in problems:
        print(f"REVIEW {path}: {issue}")
    print(f"Checked {len(paths)} allowlisted files; {len(problems)} findings.")
    print("Excluded: local notes, review pages, screenshots, builds and session data.")
    print("This is a preflight, not a guarantee: inspect the exact staged files before publishing.")
    return bool(problems)


if __name__ == "__main__":
    sys.exit(main())
