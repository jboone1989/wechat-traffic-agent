"""Incremental HNSW view; SQLite selects the atomically committed generation."""
from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path
from uuid import uuid4


class MemoryAnnIndex:
    def __init__(self,store,model_id):
        self.store,self.model_id=store,model_id
        self.root=store.root/'ann';self.root.mkdir(exist_ok=True)
        with store._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS memory_ann_state(
                node_id TEXT PRIMARY KEY,label INTEGER NOT NULL,revision TEXT NOT NULL,status TEXT NOT NULL)''')

    def search(self,vector,*,filter_sql,filter_params,limit=32):
        import hnswlib
        import numpy as np
        with (self.root/'writer.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            with self.store._connect() as db:
                generation=db.execute("SELECT value FROM memory_service_meta WHERE key='ann_generation'").fetchone()
                metadata=json.loads(generation[0]) if generation else {}
            index=hnswlib.Index(space='cosine',dim=len(vector))
            filename=self.root/metadata.get('filename','missing')
            rebuilding=metadata.get('model_id')!=self.model_id or not filename.is_file()
            if rebuilding:
                with self.store._connect() as db:
                    rows=db.execute("SELECT rowid label,* FROM memory_nodes WHERE status='ACTIVE' AND vector IS NOT NULL AND model_id=?",(self.model_id,)).fetchall()
                index.init_index(max_elements=max(1024,len(rows)*2),ef_construction=100,M=16)
                old=[]
            else:
                index.load_index(str(filename))
                with self.store._connect() as db:
                    rows=db.execute('''SELECT n.rowid label,n.* FROM memory_nodes n
                        LEFT JOIN memory_ann_state a ON a.node_id=n.node_id
                        WHERE n.status='ACTIVE' AND n.vector IS NOT NULL AND n.model_id=?
                        AND (a.node_id IS NULL OR a.revision!=n.vector_revision OR a.status!='ACTIVE')''',(self.model_id,)).fetchall()
                    old=db.execute("SELECT a.* FROM memory_ann_state a LEFT JOIN memory_nodes n ON n.node_id=a.node_id WHERE a.status='ACTIVE' AND (n.node_id IS NULL OR n.status!='ACTIVE' OR n.model_id!=?)",(self.model_id,)).fetchall()
            if index.get_current_count()+len(rows)>index.get_max_elements():
                index.resize_index((index.get_current_count()+len(rows))*2+1024)
            changes=[]
            for row in old:
                index.mark_deleted(row['label'])
                changes.append((row['node_id'],row['label'],row['revision'],'INACTIVE'))
            for row in rows:
                if not rebuilding:
                    with self.store._connect() as db:
                        previous=db.execute('SELECT status FROM memory_ann_state WHERE node_id=?',(row['node_id'],)).fetchone()
                    if previous and previous[0]!='ACTIVE':index.unmark_deleted(row['label'])
                index.add_items(np.asarray([json.loads(row['vector'])],dtype=np.float32),np.asarray([row['label']],dtype=np.int64),num_threads=1)
                changes.append((row['node_id'],row['label'],row['vector_revision'],'ACTIVE'))
            if rebuilding or changes:
                new_name=hashlib.sha256(self.model_id.encode()).hexdigest()[:12]+'-'+uuid4().hex+'.bin'
                index.save_index(str(self.root/new_name))
                with self.store._connect() as db:
                    if rebuilding:db.execute('DELETE FROM memory_ann_state')
                    db.executemany('INSERT OR REPLACE INTO memory_ann_state VALUES(?,?,?,?)',changes)
                    db.execute("INSERT OR REPLACE INTO memory_service_meta VALUES('ann_generation',?)",(json.dumps({'filename':new_name,'model_id':self.model_id}),))
                # The committed pointer is authoritative. Crash-created orphan
                # generations can be removed without affecting canonical data.
                for old_file in self.root.glob('*.bin'):
                    if old_file.name!=new_name:old_file.unlink(missing_ok=True)
            with self.store._connect() as db:
                allowed=db.execute("SELECT n.rowid label,n.node_id FROM memory_nodes n WHERE "+filter_sql+" AND n.vector IS NOT NULL AND n.model_id=?",[*filter_params,self.model_id]).fetchall()
            labels={r['label']:r['node_id'] for r in allowed}
            if not labels:return []
            count=min(limit,len(labels));index.set_ef(max(64,count*4))
            nearest,distances=index.knn_query(np.asarray([vector],dtype=np.float32),k=count,num_threads=1,filter=lambda label:label in labels)
            return [(labels[int(label)],1-float(distance)) for label,distance in zip(nearest[0],distances[0],strict=True)]
