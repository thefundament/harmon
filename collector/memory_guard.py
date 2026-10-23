#!/usr/bin/env python3
"""Run only our process tree with cgroup, GPU and available-host-memory guards."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time

GIB = 1024**3

def available_bytes():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1])*1024
    raise RuntimeError('MemAvailable missing')

def gpu_bytes():
    # Both VRAM and GTT can consume shared RAM. Sum increases conservatively;
    # this may double count pages also charged to our cgroup.
    paths = sorted(Path('/sys/class/drm').glob('card[0-9]*/device/mem_info_*_used'))
    values = {}
    for p in paths:
        if p.name not in ('mem_info_vram_used', 'mem_info_gtt_used'):
            continue
        values[str(p)] = int(p.read_text())
    return values

def cgroup_dir():
    line = next(x for x in Path('/proc/self/cgroup').read_text().splitlines() if x.startswith('0::'))
    return Path('/sys/fs/cgroup') / line[3:].lstrip('/')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--metrics',type=Path,required=True)
    p.add_argument('command',nargs=argparse.REMAINDER)
    a=p.parse_args()
    command=a.command[1:] if a.command[:1]==['--'] else a.command
    if not command: p.error('command required')
    group=cgroup_dir()
    if (group/'memory.max').read_text().strip() == 'max' or int((group/'memory.max').read_text()) > 6*GIB:
        raise RuntimeError('Run through scripts/run_limited.sh: memory.max must be <=6 GiB')
    if (group/'memory.swap.max').read_text().strip() != '0':
        raise RuntimeError('Swap must be disabled for this job')
    baseline=gpu_bytes()
    if not baseline: raise RuntimeError('GPU allocation counters unavailable; cannot enforce shared RAM budget')
    metrics={'gpu_baseline_bytes':baseline,'peak_cgroup_bytes':0,'peak_gpu_delta_bytes':0,
             'peak_combined_bytes':0,'minimum_host_available_bytes':available_bytes(),
             'limits':{'cgroup_bytes':6*GIB,'combined_bytes':8*GIB,'host_floor_bytes':2*GIB},
             'stop_reason':None}
    def snapshot():
        current=int((group/'memory.current').read_text())
        gpu=gpu_bytes()
        delta=sum(max(0,v-baseline[k]) for k,v in gpu.items())
        avail=available_bytes()
        metrics['peak_cgroup_bytes']=max(metrics['peak_cgroup_bytes'],current)
        metrics['peak_gpu_delta_bytes']=max(metrics['peak_gpu_delta_bytes'],delta)
        metrics['peak_combined_bytes']=max(metrics['peak_combined_bytes'],current+delta)
        metrics['minimum_host_available_bytes']=min(metrics['minimum_host_available_bytes'],avail)
        return ('host available memory below 2 GiB' if avail < 2*GIB else
                'combined process/GPU budget exceeded 8 GiB' if current+delta > 8*GIB else None)
    reason=snapshot()
    if reason:
        metrics['stop_reason']=reason
        a.metrics.write_text(json.dumps(metrics,indent=2)+'\n')
        print('MEMORY GUARD:',reason,flush=True)
        return 2
    child=subprocess.Popen(command,start_new_session=True)
    def forward(sig,frame):
        metrics['stop_reason']='received signal '+str(sig)
        if child.poll() is None: os.killpg(child.pid,signal.SIGTERM)
    signal.signal(signal.SIGTERM,forward)
    signal.signal(signal.SIGINT,forward)
    started=time.monotonic(); deadline=None
    try:
        while child.poll() is None:
            reason=snapshot()
            if reason and deadline is None:
                metrics['stop_reason']=reason
                print('MEMORY GUARD:',reason,flush=True)
                os.killpg(child.pid,signal.SIGTERM)
                deadline=time.monotonic()+20
            if metrics['stop_reason'] and deadline is None: deadline=time.monotonic()+20
            if deadline is not None and time.monotonic()>deadline:
                os.killpg(child.pid,signal.SIGKILL)
                break
            time.sleep(0.25)
        child.wait()
        snapshot()
    finally:
        if child.poll() is None:
            os.killpg(child.pid,signal.SIGKILL);child.wait()
        metrics['elapsed_seconds']=round(time.monotonic()-started,3)
        metrics['child_returncode']=child.returncode
        metrics['cgroup_peak_bytes']=int((group/'memory.peak').read_text())
        metrics['memory_events']=(group/'memory.events').read_text()
        a.metrics.parent.mkdir(parents=True,exist_ok=True)
        a.metrics.write_text(json.dumps(metrics,indent=2)+'\n')
        print('MEMORY METRICS',json.dumps(metrics),flush=True)
    return 2 if metrics['stop_reason'] else child.returncode

if __name__=='__main__':
    raise SystemExit(main())
