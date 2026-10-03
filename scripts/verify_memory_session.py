#!/usr/bin/env python3
"""Exercise on-demand evidence reading through the existing ChatGPT route."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

parser=argparse.ArgumentParser()
parser.add_argument('--state-root',type=Path,required=True)
parser.add_argument('--environment-pid',type=int,required=True)
args=parser.parse_args()
for entry in Path(f'/proc/{args.environment_pid}/environ').read_bytes().split(b'\0'):
    if b'=' in entry:
        key,value=entry.split(b'=',1)
        os.environ[key.decode()]=value.decode()
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from pydantic import BaseModel,Field
from wechat_traffic_agent.agent_runtime_chatgpt import AgentRuntimeChatGPTProvider
from wechat_traffic_agent.content_loop import ReceiptRuntime
from wechat_traffic_agent.memory_service import MemoryService
from wechat_traffic_agent.memory_session import MemorySession,memory_decision

original='The worker saves its invocation identity before an external effect. After a restart, it reconciles an unfinished operation with the same identity. A checkpoint alone does not prove the external operation completed.'

class Decision(BaseModel):
    answer:str=Field(max_length=400)
    evidence_ref:str
    quote:str=Field(max_length=400)

async def main():
    provider=AgentRuntimeChatGPTProvider.from_environment()
    if not provider.available: raise RuntimeError('Existing ChatGPT route unavailable')
    class Cognition:
        agent_manifest=None
        async def cognitive_probe(self,task,max_tokens,output_schema=None):
            return await provider.complete(task,max_tokens=max_tokens,reasoning_effort='low',
                _routing_task_class='owner_dialogue')
    folder=args.state_root/'acceptance'
    runtime=ReceiptRuntime(Cognition(),folder/'model_calls')
    service=MemoryService(args.state_root)
    root=service.ingest_source({'evidence_id':'acceptance-checkpoint','title':'Recovery acceptance fixture',
        'evidence_text':original,'summary':original,'url':'https://example.com/acceptance'},topic='restart')
    session=MemorySession(folder,'read',service)
    if not session.state['cards']:
        session.state['cards']=session.cards(service.expand(root));session.save()
    def validate(value):
        result=session.state.get('tool_result') or {}
        if not result.get('ref'): raise ValueError('Read the original with READ_EVIDENCE before the final decision')
        if value.evidence_ref!=result['ref'] or not value.quote or value.quote not in original:
            raise ValueError('Cite the read original using its exact task reference and excerpt')
    decision=await memory_decision(runtime=runtime,session=session,
        instruction='ACCEPTANCE: First use READ_EVIDENCE on the supplied memory reference, then answer in Chinese: does a checkpoint prove an external operation completed? Cite a short exact English quote from the original. ',
        payload_builder=lambda:{'question':'Does a checkpoint prove an external operation completed?'},
        contract_builder=lambda:Decision,validate=validate,max_turns=4)
    metrics=[json.loads(line) for line in (folder/'memory_request_sizes.jsonl').read_text().splitlines()]
    report={'status':'PASS','decision':decision.model_dump(),'tool_turns':session.state['turns'],
        'request_characters':[m['characters'] for m in metrics],
        'max_estimated_total_tokens':max(m['estimated_input_tokens']+m['reserved_output_tokens'] for m in metrics),
        'scope':'Synthetic original, real shared ChatGPT route; no publication'}
    (folder/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False),flush=True)

asyncio.run(main())
