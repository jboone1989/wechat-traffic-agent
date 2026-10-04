"""Verify actual checkpoint migration on an isolated snapshot of live memory."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--state-root', type=Path, required=True)
parser.add_argument('--output-root', type=Path, required=True)
parser.add_argument('--runtime-source', type=Path)
args = parser.parse_args()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
if args.runtime_source:
    sys.path.insert(0, str(args.runtime_source))
from wechat_traffic_agent.memory_service import MemoryService
from agent_runtime.memory.session import MemorySession
import agent_runtime.memory.session as controller

output = args.output_root.resolve()
state = args.state_root.resolve(strict=True)
if output.is_relative_to(state) or state.is_relative_to(output):
    raise ValueError('verification output must be separate from production state')
output.mkdir(parents=True, exist_ok=False)
source = state / 'application/memory_system/memory.db'
dest = output / 'snapshot/application/memory_system/memory.db'
dest.parent.mkdir(parents=True)
with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as live:
    with sqlite3.connect(dest) as snapshot:
        live.backup(snapshot, pages=256)
shutil.copytree(source.parent / 'evidence', dest.parent / 'evidence')
service = MemoryService(output / 'snapshot')
files = sorted((state / 'content/continuous/cycles').glob('*/memory_session_*.json'),
               key=lambda path: path.stat().st_mtime, reverse=True)[:12]
if not files:
    raise RuntimeError('no real production memory checkpoints found')
results = []
for index, path in enumerate(files):
    raw = path.read_bytes()
    old = json.loads(raw)
    folder = output / 'checkpoints' / str(index)
    folder.mkdir(parents=True)
    (folder / path.name).write_bytes(raw)
    session = MemorySession(folder, old['phase'], service)
    assert session.state['refs'] == old['refs']
    for field in ('working_summary', 'missing_information', 'tool_result',
                  'decision_bindings', 'application_bindings', 'external_searches'):
        if field in old:
            assert session.state[field] == old[field], field
    packet = session.packet()
    session.save()
    results.append({'source': str(path.relative_to(state)), 'original_sha256': hashlib.sha256(raw).hexdigest(),
                    'old_schema': old['schema'], 'new_schema': session.state['schema'],
                    'status': session.state['status'], 'refs': len(session.state['refs']),
                    'visible_cards': len(packet['memory_cards']),
                    'remaining_rounds': session.remaining_rounds,
                    'budget_origin': session.state['budget_origin'], 'preserved': True})
receipt = {'schema': 'runtime-memory-session.snapshot-verification.v1',
           'controller': controller.__file__, 'production_changed': False,
           'checkpoint_count': len(results), 'checkpoints': results}
(output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt))
