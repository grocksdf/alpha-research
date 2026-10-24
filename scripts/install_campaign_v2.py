#!/usr/bin/env python3
"""Apply additive AlphaResearch v0.6.1 offline campaign patch, safely.

Does not clone, edit git history, modify legacy files, execute an API call,
modify a database record, or rewrite a frozen snapshot.
"""
from __future__ import annotations
import argparse
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

FILES=[
    "alpharesearch/campaign_core.py",
    "alpharesearch/campaign_runner.py",
    "alpharesearch/campaign_overfit.py",
    "alpharesearch/campaign_evidence.py",
    "alpharesearch/campaign_cli.py",
    "alpharesearch/campaign_live_policy.py",
    "tests/test_campaign_v2.py",
    "verification/acceptance.py",
]

def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo",required=True,help="Path to existing AlphaResearch clone")
    ap.add_argument("--apply",action="store_true",help="Actually install new files")
    args=ap.parse_args(argv)
    origin=Path(__file__).resolve().parent
    dest=Path(args.repo).resolve()
    required=("alpharesearch/autonomy.py","alpharesearch/engine.py",
              "alpharesearch/memory.py","alpharesearch/discovery.py",
              "alpharesearch/common.py","config/settings.toml","program.md")
    if any(not (dest/name).is_file() for name in required):
        ap.error("Target is not an AlphaResearch checkout; required files missing")
    if "autonomous-discovery-mvp-v1" not in (dest/"alpharesearch/autonomy.py").read_text(encoding="utf-8"):
        ap.error("Unsupported legacy autonomy protocol. Review manually before adding campaign v2")
    status=[]
    for relative in FILES:
        from_file=origin/relative
        to_file=dest/relative
        if not from_file.is_file():
            ap.error("Patch is incomplete: "+relative)
        if to_file.exists() and sha256(to_file)!=sha256(from_file):
            ap.error("Target file already exists with DIFFERENT content (refusing overwrite): "+relative)
        status.append((relative,not to_file.exists(),sha256(from_file)))
    print("AlphaResearch Campaign v2 ADDITIVE installer")
    print("Repository:",dest)
    print("Mode:","APPLY" if args.apply else "DRY RUN (add --apply to write)")
    for name,needed,sha in status:
        print(("ADD" if needed else "UNCHANGED")+" "+name+" sha256="+sha)
    if not args.apply:
        print("No files written. Original code, frozen hashes and SQLite were not touched.")
        return 0
    for relative,needed,expected in status:
        if not needed:
            continue
        destination=dest/relative
        destination.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix=".campaign_v2_",dir=destination.parent)
        try:
            with os.fdopen(fd,"wb") as out, (origin/relative).open("rb") as source:
                shutil.copyfileobj(source,out)
                out.flush();os.fsync(out.fileno())
            if destination.exists():
                raise FileExistsError("Concurrent installer wrote "+str(destination))
            os.replace(tmp,destination)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    print("Installed additive files. IMPORTANT: immutable code snapshot will now differ.")
    print("Legacy runs must remain frozen. Use campaign import-legacy for safe migration.")
    print("Next: python -m unittest discover -s tests -p 'test_*.py' -v")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
