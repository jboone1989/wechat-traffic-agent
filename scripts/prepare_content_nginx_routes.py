#!/usr/bin/env python3
"""Prepare a bounded Nginx change that routes published content independently."""
from pathlib import Path
import json
import sys

original, prepared = map(Path, sys.argv[1:3])
text = original.read_text(encoding="utf-8")
marker = "    location / { limit_except GET HEAD { deny all; } add_header Cache-Control"
if text.count(marker) != 1:
    raise RuntimeError("unexpected nginx location block: do not edit")
if "content-current" in text:
    raise RuntimeError("Nginx data routing already installed; stop instead of duplicating")
locations = """    # Content comes from an independent, atomically updated data snapshot.
    # The website design is served by /var/www/content-agent-live/current.
    location ~ ^/(articles|guides|research|notes|archive|topics)/ {
        limit_except GET HEAD { deny all; }
        root /var/www/content-agent-live/content-current;
        index index.html;
        try_files $uri $uri/ =404;
        add_header Cache-Control "no-cache" always;
    }
    location = /data/articles.json {
        limit_except GET HEAD { deny all; }
        root /var/www/content-agent-live/content-current;
        add_header Cache-Control "no-cache" always;
    }
    location = /sitemap.xml {
        limit_except GET HEAD { deny all; }
        root /var/www/content-agent-live/content-current;
        add_header Cache-Control "no-cache" always;
    }
    location = /feed.xml {
        limit_except GET HEAD { deny all; }
        root /var/www/content-agent-live/content-current;
        add_header Cache-Control "no-cache" always;
    }
"""
prepared.parent.mkdir(parents=True, exist_ok=True)
prepared.write_text(text.replace(marker, locations + marker), encoding="utf-8")
prepared.chmod(0o644)
print(json.dumps({"status":"PREPARED","path":str(prepared),"bytes":prepared.stat().st_size}))
