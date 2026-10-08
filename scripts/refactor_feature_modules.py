"""One-time mechanical move of Ferro feature modules into named packages.

Run: python3 scripts/refactor_feature_modules.py --check
     python3 scripts/refactor_feature_modules.py --apply

The original module paths remain compatibility aliases, not duplicate implementations.
No runtime state, databases or deployment files are touched.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1] / "src" / "wechat_traffic_agent"
GROUPS = {
    "editorial": (
        "article_repository", "content_artifact", "content_admission",
        "content_publication_store", "draft_inbox", "editorial_policy",
        "editorial_intervention", "channel_evidence",
    ),
    "website": (
        "automatic_website", "site_audit", "web_editorial",
        "website_engagement", "website_engagement_client",
        "website_experiment", "website_experiment_evaluation",
        "web_outcome_evidence", "gsc_search_console",
    ),
    "platforms.browser": (
        "browser_accounts", "browser_runtime", "browser_session_stream",
        "local_browser_control", "account_management", "account_registry",
        "novnc_takeover",
    ),
    "platforms.x": (
        "x_article_media", "x_browser", "x_browser_mentions",
        "x_discovery", "x_goal_progress", "x_growth_mission",
        "x_image_generation", "x_jev_article", "x_jev_metrics",
        "x_jev_social", "x_jev_transaction", "x_local_browser",
        "x_mcp", "x_oauth_headless", "x_outcomes",
        "x_owner_execution", "x_social",
    ),
}

IMPORT_FROM = re.compile(r"(?m)^([ \t]*from[ \t]+)\.([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)([ \t]+import\b)")
IMPORT_DOT = re.compile(r"(?m)^([ \t]*from[ \t]+)\.([ \t]+import\b)")


def relocated(source: str, module: str) -> str:
    syntax = ast.parse(source, filename=module + ".py")
    for item in ast.walk(syntax):
        if isinstance(item, ast.ImportFrom) and item.level > 1:
            raise ValueError(f"unsupported parent-relative import in {module}: {item.module}")
    translated = IMPORT_FROM.sub(r"\1wechat_traffic_agent.\2\3", source)
    translated = IMPORT_DOT.sub(r"\1wechat_traffic_agent\2", translated)
    for item in ast.walk(ast.parse(translated, filename=module + ".py")):
        if isinstance(item, ast.ImportFrom) and item.level:
            raise ValueError(f"unresolved relative import in {module}: {item.module}")
    return translated


def alias(group: str, module: str) -> str:
    destination = f"wechat_traffic_agent.{group}.{module}"
    return (
        f'"""Compatibility import: implementation lives in {destination}."""\n'
        "from importlib import import_module as _import_module\n"
        "import sys as _sys\n\n"
        f"_sys.modules[__name__] = _import_module({destination!r})\n"
    )


def run(apply: bool) -> None:
    moved = []
    for group, modules in GROUPS.items():
        package = ROOT.joinpath(*group.split("."))
        for module in modules:
            old = ROOT / f"{module}.py"
            new = package / f"{module}.py"
            if new.exists():
                if not old.exists() or "_sys.modules[__name__] = _import_module(" not in old.read_text():
                    raise FileExistsError(f"partial migration, inspect {old} and {new}")
                if apply and old.read_text(encoding="utf-8") != alias(group, module):
                    old.write_text(alias(group, module), encoding="utf-8")
                continue
            if not old.is_file():
                raise FileNotFoundError(old)
            original = old.read_text(encoding="utf-8")
            translated = relocated(original, module)
            moved.append((old, new, translated, alias(group, module)))
    print(f"{len(moved)} modules need moving")
    for old, new, content, shim in moved:
        print(f"{old.name} -> {new.relative_to(ROOT)}")
        if not apply:
            continue
        new.parent.mkdir(parents=True, exist_ok=True)
        parents = [ROOT / "/".join(new.relative_to(ROOT).parts[:i]) for i in range(1, len(new.relative_to(ROOT).parts))]
        for directory in parents:
            if not (directory / "__init__.py").exists():
                (directory / "__init__.py").write_text('"""Ferro feature package."""\n', encoding="utf-8")
        new.write_text(content, encoding="utf-8")
        old.write_text(shim, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    run(args.apply)
