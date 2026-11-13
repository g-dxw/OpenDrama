#!/usr/bin/env python3
"""Build JJTV frontends from a clean HEAD and record private release evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
FRONTENDS = ("web", "h5", "admin")
API_BASE = "https://jjtv-api.8jj.net"


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def require_clean_source(root: Path, frontends: tuple[str, ...] = FRONTENDS) -> str:
    sha = git(root, "rev-parse", "HEAD")
    changed = git(root, "diff", "--name-only", "HEAD", "--")
    if changed:
        raise SystemExit("Commit tracked source changes before building or deploying JJTV:\n" + changed)
    for frontend in frontends:
        untracked = git(root, "ls-files", "--others", "--exclude-standard", "--", frontend)
        if untracked:
            raise SystemExit(f"Untracked {frontend} source is not part of HEAD:\n{untracked}")
    return sha


def manifest_path(root: Path, sha: str, frontend: str) -> Path:
    common = Path(git(root, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = root / common
    return common / "jjtv-builds" / sha / f"{frontend}.json"


def asset_hashes(dist: Path) -> dict[str, str]:
    if dist.is_symlink() or not dist.is_dir() or not (dist / "index.html").is_file():
        raise SystemExit(f"Missing regular frontend build directory: {dist}")
    hashes: dict[str, str] = {}
    for file in sorted(dist.rglob("*")):
        if file.is_symlink():
            raise SystemExit(f"Symlinks are not permitted in frontend builds: {file}")
        if file.is_dir():
            continue
        if not file.is_file():
            raise SystemExit(f"Unsupported frontend build entry: {file}")
        hashes[file.relative_to(dist).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    return hashes


def assert_same_source(root: Path, sha: str, frontends: tuple[str, ...]) -> None:
    if require_clean_source(root, frontends) != sha:
        raise SystemExit("HEAD changed while building or packaging JJTV; rebuild from the committed source.")


def write_manifest(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default="node", help="Node executable; an absolute Windows/WSL path is supported.")
    parser.add_argument("--frontend", choices=FRONTENDS, action="append", help="Build one frontend; repeat as needed. Default: all three.")
    args = parser.parse_args()
    frontends = tuple(dict.fromkeys(args.frontend or FRONTENDS))
    node = shutil.which(args.node)
    if not node:
        raise SystemExit(f"Node executable not found: {args.node}")
    sha = require_clean_source(ROOT, frontends)
    environment = os.environ.copy()
    environment["VITE_API_BASE"] = API_BASE
    for frontend in frontends:
        directory = ROOT / frontend
        manifest = manifest_path(ROOT, sha, frontend)
        manifest.unlink(missing_ok=True)
        print(f"Building {frontend}: source={sha}; VITE_API_BASE={API_BASE}", flush=True)
        # Relative arguments also work when WSL launches the Windows Node binary.
        for command in (
            [node, "node_modules/typescript/bin/tsc", "--noEmit"],
            [node, "node_modules/vite/bin/vite.js", "build"],
        ):
            subprocess.run(command, cwd=directory, env=environment, check=True)
        assert_same_source(ROOT, sha, frontends)
        hashes = asset_hashes(directory / "dist")
        assert_same_source(ROOT, sha, frontends)
        write_manifest(manifest, {
            "schema_version": 1,
            "source_sha": sha,
            "frontend": frontend,
            "api_base": API_BASE,
            "assets": hashes,
        })
        print(f"Verified {frontend}: {len(hashes)} files; private manifest={manifest}", flush=True)
    assert_same_source(ROOT, sha, frontends)


if __name__ == "__main__":
    main()
