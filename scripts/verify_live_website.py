"""One-shot audit of Ferro's currently published first-party website."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

def public_environment():
    wanted = {"CONTENT_AUTO_SITE_CONFIG", "CONTENT_AUTO_PUBLIC_ROOT",
              "CONTENT_AUTO_PUBLISH_ENABLED", "CONTENT_WEB_BASE_URL"}
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            cmd = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            if "run_content_cognition_worker.py" not in cmd:
                continue
            env = {}
            for pair in (p / "environ").read_bytes().split(b"\0"):
                if b"=" not in pair:
                    continue
                key, _, val = pair.partition(b"=")
                key = key.decode(errors="replace")
                if key in wanted:
                    env[key] = val.decode(errors="replace")
            if wanted - set(env):
                raise RuntimeError("website publishing environment is incomplete")
            return env
        except (OSError, ValueError):
            continue
    raise RuntimeError("live Ferro content worker not available")

def main():
    from wechat_traffic_agent.automatic_website import AutomaticWebsitePublisher
    from wechat_traffic_agent.channels.web import StaticWebsiteConfig
    env = public_environment()
    if env["CONTENT_AUTO_PUBLISH_ENABLED"].lower() not in {"1","true","yes","on"}:
        raise RuntimeError("automatic first-party publication is not enabled")
    config = StaticWebsiteConfig.model_validate_json(Path(env["CONTENT_AUTO_SITE_CONFIG"]).read_text())
    release = Path(__file__).resolve().parents[1]
    pub = AutomaticWebsitePublisher(state_root="/home/ubuntu/content-agent-state",
        public_root=env["CONTENT_AUTO_PUBLIC_ROOT"],
        starter_path=release / "content/starter", config=config)
    receipt = pub.maintain(now=datetime.now(timezone.utc))
    from wechat_traffic_agent.site_audit import _PageParser
    problematic = []
    current = pub.public / "current"
    for page in current.rglob("*.html"):
        parser = _PageParser()
        parser.feed(page.read_text(encoding="utf-8"))
        if not (parser.has_title and parser.has_description and parser.has_canonical):
            problematic.append({"path": str(page.relative_to(current)),
                "title": parser.has_title, "description": parser.has_description,
                "canonical": parser.has_canonical})
    print(json.dumps({"website_url":config.base_url,"status":receipt.get("status"),
        "release":receipt.get("release"),"articles":receipt.get("article_count"),
        "topics":receipt.get("topic_count"),"audit":receipt.get("audit"),
        "problem_pages":problematic[:40],"problem_total":len(problematic)},ensure_ascii=False))
    if receipt.get("status") != "HEALTHY":
        raise RuntimeError("live website audit did not pass")

if __name__ == "__main__":
    main()
