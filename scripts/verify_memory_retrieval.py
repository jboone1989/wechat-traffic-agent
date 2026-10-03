#!/usr/bin/env python3
"""Small real-model recall smoke check; reports both first and top-three ranks."""
import sys,json,time,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from wechat_traffic_agent.memory_service import MemoryService
parser=argparse.ArgumentParser()
parser.add_argument('--state-root',type=Path,required=True)
args=parser.parse_args()
root=args.state_root
service=MemoryService(root)
facts=[('restart','A worker persists invocation identity before external effects and reconciles unfinished operations after reboot.'),('cost','Replace disposable workloads first when hardware capacity is overloaded.'),('permission','The browser requires an explicit owner authorization before a trade can be submitted.'),('weather','The forecast predicts heavy rain on the coast tomorrow.'),('payment','A payment retry retains the same idempotency key so a network timeout cannot produce two transfers.'),('summary','A summary tree descends into original source passages rather than treating summaries as proof.')]
facts.append(('resource-cn','资源压力出现时，先优化现有容量；不足再迁移可重建工作到低信任机器。'))
for key,text in facts:
 if not service.store.get_memory('eval:'+key):service.store.append_memory({'memory_id':'eval:'+key,'text':text,'kind':'FACT','topics':[key]})
queries=[('restart','recover pending tool calls when the process crashes'),('cost','machine resource pressure should shed replaceable jobs'),('payment','avoid charging twice after a request loses its response'),('summary','verify evidence behind a hierarchical condensed report'),('restart','工作进程崩溃后怎样恢复未完成的工具操作'),('payment','网络超时重试怎样避免重复转账')]
queries.append(('resource-cn','If hardware is overloaded and capacity is tight, what comes before moving disposable work to an unreliable machine?'))
results=[]
for expected,query in queries:
 start=time.monotonic();hits=service.search(query,limit=3,node_types=['memory'])
 result={'query':query,'expected':'eval:'+expected,'ranked_ids':[h['node_id'] for h in hits],'seconds':round(time.monotonic()-start,2),'hit_at_1':bool(hits and hits[0]['node_id']=='eval:'+expected),'hit_at_3':any(h['node_id']=='eval:'+expected for h in hits)}
 results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
report={'encoder':service._models().model_id,'reranker':service._models().reranker,'cases':results,'hit_at_1':sum(r['hit_at_1'] for r in results),'hit_at_3':sum(r['hit_at_3'] for r in results),'total':len(results)}
(root/'retrieval_acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
raise SystemExit(0 if all(r['hit_at_3'] for r in results) else 1)
