"""Activate a prepared Ferro release and write its verification receipt."""
import argparse, json, os, subprocess, uuid
from datetime import datetime, timezone
from pathlib import Path
parser=argparse.ArgumentParser()
parser.add_argument("--release",type=Path,required=True)
parser.add_argument("--expected-current",type=Path,required=True)
parser.add_argument("--commit",required=True)
parser.add_argument("--evidence-ref",required=True)
args=parser.parse_args()
current=Path.home()/"content-agent/current"
release=args.release.resolve(strict=True)
previous=current.resolve(strict=True)
if previous!=args.expected_current.resolve(strict=True):
    raise RuntimeError("Production release changed before activation")
actual=subprocess.check_output(["git","-C",str(release),"rev-parse","HEAD"],text=True).strip()
if actual!=args.commit:
    raise RuntimeError("Prepared release commit differs from requested commit")
manifest={"schema":"pi-remote.ferro-release.v1","gitCommit":actual,
    "releaseId":release.name,"releasePath":str(release),
    "materializedAt":datetime.now(timezone.utc).isoformat(),
    "previousRelease":str(previous),
    "evidenceRef":args.evidence_ref,
    "editorialDomains":["CRYPTOCURRENCY","AI","FINANCE"]}
(release/".ferro-release.json").write_text(json.dumps(manifest,indent=2)+"\n")
temporary=current.with_name("current-posting-"+uuid.uuid4().hex)
temporary.symlink_to(release)
os.replace(temporary,current)
print(json.dumps(manifest))
