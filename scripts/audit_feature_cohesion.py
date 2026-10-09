"""Read-only audit for splitting Ferro's large orchestrators by responsibility."""
import ast
from pathlib import Path
from collections import Counter

root=Path(__file__).resolve().parents[1]/"src"/"wechat_traffic_agent"
files=("content_goal_loop.py","wakeful_tasks.py","wakeful_runtime.py","live_agent.py","content_mind.py","learning_research.py","memory_service.py","runtime_app.py")
for filename in files:
    path=root/filename
    source=path.read_text()
    t=ast.parse(source)
    print("\n###",filename, len(source.splitlines()),"lines")
    for n in t.body:
        if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
            span=(n.end_lineno-n.lineno+1)
            if isinstance(n,ast.ClassDef):
                funcs=[f for f in n.body if isinstance(f,(ast.FunctionDef,ast.AsyncFunctionDef))]
                print(f"class {n.name}: {span} lines, {len(funcs)} methods")
                for f in funcs:
                    print(f"  {f.name}: {f.end_lineno-f.lineno+1} lines")
            else:
                print(f"{n.name}: {span} lines")
