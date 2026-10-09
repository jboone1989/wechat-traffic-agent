#!/usr/bin/env python3
"""Read-only slab and browser process diagnosis for VM. No cache flush/kill."""
from pathlib import Path
from collections import Counter
import json
import os
import time

C=Path("/sys/fs/cgroup/system.slice/content-agent.service")


def read(path):
    try: return Path(path).read_text(errors="replace")
    except (OSError, UnicodeError): return ""


def browser_processes():
    result=[]
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():continue
        raw=read(entry/"cmdline")
        argv=raw.split(chr(0))
        if not any("browser-harness" in a or "browser_harness" in a for a in argv):continue
        st=read(entry/"status")
        info={}
        for line in st.splitlines():
            if line.startswith(("PPid:","VmRSS:","VmSwap:","Threads:")):
                k,v=line.split(":",1);info[k]=v.strip()
        try:
            fds=list((entry/"fd").iterdir())
        except OSError:
            fds=[]
        counts=Counter()
        for fd in fds[:2048]:
            try: target=os.readlink(fd)
            except OSError:continue
            if target.startswith("socket:"):kind="socket"
            elif target.startswith("pipe:"):kind="pipe"
            elif target.startswith("anon_inode:"):kind="anon_inode"
            else:kind="file"
            counts[kind]+=1
        result.append({"pid":int(entry.name),"parent_pid":info.get("PPid"),
                      "rss":info.get("VmRSS"),"swap":info.get("VmSwap"),
                      "threads":info.get("Threads"),"fd_count":len(fds),
                      "fd_types":dict(counts),
                      "program":"daemon" if "-m"+chr(0)+"browser_harness.daemon" in raw else "CLI"})
    return sorted(result,key=lambda x:x["pid"])


def cgroup_metrics():
    data={}
    for row in read(C/"memory.stat").splitlines():
        parts=row.split()
        if len(parts)==2 and parts[1].isdigit() and parts[0] in (
          "anon","file","kernel","slab","slab_reclaimable","slab_unreclaimable",
          "inactive_file","active_file","file_dirty","file_writeback"):
            data[parts[0]]=round(int(parts[1])/1048576,2)
    return data


def state_directory_counts():
    root=Path("/home/ubuntu/content-agent-state")
    dirs=["application/state_events/applied","application/state_events/pending",
          "content/learning","content/x","content/continuous",
          "application/workers","content/website","memory"]
    out={}
    for name in dirs:
        path=root/name
        if not path.is_dir():
            continue
        total=0
        suffixes=Counter()
        try:
            with os.scandir(path) as it:
                for entry in it:
                    total+=1
                    if entry.is_file(follow_symlinks=False):
                        suffixes[Path(entry.name).suffix]+=1
                    if total>=200000:
                        break
            out[name]={"entries":total, "limit_reached":total==200000,
                       "suffixes":dict(suffixes.most_common(4))}
        except OSError:
            out[name]={"error":"directory unavailable"}
    return out


def kernel_counters():
    result={}
    for name in ("dentry-state","inode-nr","file-nr"):
        result[name]=read("/proc/sys/fs/"+("dentry-state" if name=="dentry-state" else name)).strip()
    return result


def browser_overlap(seconds=8):
    snapshots=[]
    for _ in range(seconds):
        procs=browser_processes()
        snapshots.append({"count":len(procs),"parents":sorted(
            {str(p["parent_pid"]) for p in procs if p["program"]=="CLI"})})
        time.sleep(1)
    return {"max_helpers":max((x["count"] for x in snapshots),default=0),
            "max_cli_calls":max((len(x["parents"]) for x in snapshots),default=0),
            "sample_count":len(snapshots)}

def applied_receipts_summary():
    folder=Path("/home/ubuntu/content-agent-state/application/state_events/applied")
    if not folder.is_dir():
        return {}
    size=0
    buckets=Counter()
    now=time.time()
    files=[]
    # Metadata only; sample file bodies separately, never dump event payloads.
    with os.scandir(folder) as it:
        for entry in it:
            if not entry.is_file(follow_symlinks=False): continue
            try: stat=entry.stat(follow_symlinks=False)
            except OSError:continue
            size+=stat.st_size
            age=now-stat.st_mtime
            key=("older_7d" if age>7*86400 else
                 "older_1d" if age>86400 else
                 "older_1h" if age>3600 else "recent_1h")
            buckets[key]+=1
            if len(files)<15:
                files.append(entry.path)
    kinds=Counter()
    requests=Counter()
    for path in files:
        try:
            row=json.loads(Path(path).read_text())
        except (OSError,ValueError):
            continue
        kinds[str(row.get("kind") or "?")]+=1
        requests[str(bool(row.get("expects_reply",False)))]+=1
    return {"count":sum(buckets.values()),"bytes_mib":round(size/1048576,2),
            "age_buckets":dict(buckets),"first_15_kind_sample":dict(kinds),
            "first_15_expects_reply_sample":dict(requests)}

def browser_artifact_inventory():
    root=Path("/home/ubuntu/.local/state/agent-runtime/browser-harness")
    if not root.is_dir():return {}
    count=Counter()
    sizes=Counter()
    now=time.time()
    ages=Counter()
    try:
        with os.scandir(root) as iterator:
            for entry in iterator:
                if not entry.is_file(follow_symlinks=False):continue
                if entry.name.startswith("chatgpt-profile-") and "rejected" in entry.name:
                    kind="rejected_conversation"
                elif entry.name.startswith("chatgpt-profile-"):
                    kind="other_conversation"
                elif entry.name.startswith("chatgpt-outbound-"):
                    kind="outbound_payload"
                elif entry.name.endswith(".lock"):
                    kind="lock"
                else:
                    kind="other"
                try:info=entry.stat(follow_symlinks=False)
                except OSError:continue
                count[kind]+=1
                sizes[kind]+=info.st_size
                ages[kind]+=int(now-info.st_mtime>86400)
    except OSError:return {"error":"directory unavailable"}
    return {"count_by_type":dict(count),"bytes_mib_by_type":{
      key:round(val/1048576,2) for key,val in sizes.items()},
      "files_older_than_day":dict(ages)}

def persistent_browser_endpoints():
    root=Path("/home/ubuntu/.local/state/agent-runtime/browser/profiles")
    out={}
    for name in ("x","chatgpt","acceptance-stack"):
        p=root/name/"runtime.json"
        if not p.is_file():
            continue
        try:
            data=json.loads(p.read_text())
        except (ValueError,OSError):
            out[name]={"readable":False}
            continue
        # Expose no session cookies, account secrets, URLs or tokens.
        port=data.get("cdp_port")
        out[name]={"cdp_port":port if isinstance(port,int) else None,
                   "active_pid":data.get("pid") if isinstance(data.get("pid"),int) else None}
    return out

def main():
    result={"observed_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
       "cgroup_stat_mib":cgroup_metrics(),
       "slab_breakdown_unavailable_without_privileged_kernel_access": True,
       "browser_harness_processes":browser_processes(),
       "state_directories":state_directory_counts(),
       "applied_receipts":applied_receipts_summary(),
       "kernel_counters":kernel_counters(),
              "browser_artifacts":browser_artifact_inventory(),
       "persistent_browser_endpoints":persistent_browser_endpoints(),
       "browser_overlap_8s":browser_overlap()}
    print(json.dumps(result,ensure_ascii=False))


if __name__=="__main__":main()
