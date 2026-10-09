"""Audit Ferro functions by length and module without importing application code."""
from __future__ import annotations
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "wechat_traffic_agent"
entries = []
for path in ROOT.rglob("*.py"):
    try: tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError): continue
    def visit(nodes, prefix=""):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + node.name
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                length = node.end_lineno - start + 1
                entries.append((length, path.relative_to(ROOT).as_posix(), name, node.lineno))
                visit(node.body, name + ".")
            elif isinstance(node, ast.ClassDef):
                visit(node.body, prefix + node.name + ".")
    visit(tree.body)
for size, path, name, line in sorted(entries, reverse=True)[:90]:
    print(f"{size:5} {path}:{line} {name}")
print("TOTAL",len(entries),"OVER_100",sum(e[0]>100 for e in entries),"OVER_50",sum(e[0]>50 for e in entries))
