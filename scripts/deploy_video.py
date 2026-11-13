#!/usr/bin/env python3
"""Deploy the isolated JJTV stack on video, only inside scripts/release.py."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import tarfile

from build_frontends import API_BASE, FRONTENDS, assert_same_source, asset_hashes, manifest_path, require_clean_source

ROOT = Path(__file__).resolve().parent.parent
HOSTS = ("jjtv.8jj.net", "jjtv-h5.8jj.net", "jjtv-admin.8jj.net", "jjtv-api.8jj.net")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def require_release_lock(sha: str) -> dict:
    import fcntl

    common = Path(git("rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = ROOT / common
    lock = common / "minitv-release.lock"
    if not lock.exists():
        raise SystemExit("Use scripts/release.py run --target video -- python3 scripts/deploy_video.py ...")
    with lock.open("r+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            fcntl.flock(handle, fcntl.LOCK_UN)
            raise SystemExit("The repository release lock is not held; deploy through scripts/release.py.")
        handle.seek(0)
        meta = json.load(handle)
        if meta.get("target") != "video":
            raise SystemExit("The active release target must be video.")
        if meta.get("pid") != os.getppid() or meta.get("host") != socket.gethostname():
            raise SystemExit("deploy_video.py must be the direct child of the process holding the video release lock.")
        version = meta.get("version")
        if not isinstance(version, str) or not version:
            raise SystemExit("The release lock has no allocated version tag.")
        try:
            tagged_sha = git("rev-parse", "--verify", f"refs/tags/{version}^{{commit}}")
        except subprocess.CalledProcessError as exc:
            raise SystemExit("The active release tag is missing or invalid.") from exc
        if tagged_sha != sha:
            raise SystemExit("The release tag does not point to HEAD; do not deploy with a different --sha.")
        return meta


def verified_frontend(frontend: str, sha: str) -> dict[str, str]:
    path = manifest_path(ROOT, sha, frontend)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Missing or invalid {frontend} build evidence; run scripts/build_frontends.py after committing.") from exc
    if not isinstance(manifest, dict) or (
        manifest.get("schema_version") != 1
        or manifest.get("source_sha") != sha
        or manifest.get("frontend") != frontend
        or manifest.get("api_base") != API_BASE
    ):
        raise SystemExit(f"{frontend} build evidence does not match HEAD and the JJTV API endpoint; rebuild it.")
    actual = asset_hashes(ROOT / frontend / "dist")
    if manifest.get("assets") != actual:
        raise SystemExit(f"{frontend}/dist changed, has extra/missing files, or was not built from this HEAD; rebuild it.")
    return actual


def payload(sha: str) -> tuple[bytes, str]:
    assert_same_source(ROOT, sha, FRONTENDS)
    builds = {frontend: verified_frontend(frontend, sha) for frontend in FRONTENDS}
    source = subprocess.check_output(
        ["git", "archive", "--format=tar", sha, "backend", "deploy/video", "cli"], cwd=ROOT
    )
    output = io.BytesIO()
    hashes: dict[str, str] = {}
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        with tarfile.open(fileobj=io.BytesIO(source), mode="r:") as committed:
            for member in committed.getmembers():
                archive.addfile(member, committed.extractfile(member) if member.isfile() else None)
        for frontend, files in builds.items():
            for name, expected_hash in files.items():
                relative = f"{frontend}/dist/{name}"
                data = (ROOT / relative).read_bytes()
                if hashlib.sha256(data).hexdigest() != expected_hash:
                    raise SystemExit(f"Frontend output changed while packaging: {relative}")
                hashes[relative] = expected_hash
                info = tarfile.TarInfo(relative)
                info.size = len(data)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(data))
        receipt = json.dumps({"source_sha": sha, "api_base": API_BASE, "hosts": HOSTS, "assets": hashes}, indent=2).encode()
        info = tarfile.TarInfo("jjtv-release.json")
        info.size = len(receipt)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(receipt))
    assert_same_source(ROOT, sha, FRONTENDS)
    for frontend, files in builds.items():
        if asset_hashes(ROOT / frontend / "dist") != files:
            raise SystemExit(f"{frontend}/dist changed while packaging; rebuild it.")
    identifier = sha[:12] + "-" + hashlib.sha256(receipt).hexdigest()[:12]
    return output.getvalue(), identifier


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("JJTV_DEPLOY_HOST", "video"))
    parser.add_argument("--phase", choices=("prepare", "http", "tls"), default="prepare")
    parser.add_argument("--check", action="store_true", help="Read-only preflight; no uploads or server writes.")
    args = parser.parse_args()
    ssh = [os.environ.get("JJTV_SSH_BIN", "ssh")]
    ssh += shlex.split(os.environ.get("JJTV_SSH_OPTIONS", ""))
    ssh += ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", args.host]
    sha = require_clean_source(ROOT)
    if not args.check:
        require_release_lock(sha)
    data, identifier = payload(sha)
    release_dir = "/opt/jjtv/releases/" + identifier
    if args.check:
        print(f"JJTV source={sha}; payload={len(data)} bytes; phase={args.phase}")
        subprocess.run(ssh + [
            "docker compose version && docker network inspect dastaan-staging_default >/dev/null "
            "&& docker inspect dastaan-staging-edge-1 >/dev/null "
            "&& test -d /opt/dastaan/shared/edge && test -d /opt/dastaan/shared/acme "
            "&& printf 'JJTV server preflight passed\\n'"
        ], check=True)
        return
    print(f"Deploying committed source {sha} into {release_dir}; phase={args.phase}")
    subprocess.run(
        ssh + [f"umask 022; mkdir -p {shlex.quote(release_dir)} && tar -xzf - -C {shlex.quote(release_dir)}"],
        input=data, check=True,
    )
    script = subprocess.check_output(["git", "show", sha + ":deploy/video/remote-deploy.sh"], cwd=ROOT)
    command = "bash -s -- " + " ".join(shlex.quote(x) for x in (release_dir, sha, args.phase))
    subprocess.run(ssh + [command], input=script, check=True)


if __name__ == "__main__":
    main()
