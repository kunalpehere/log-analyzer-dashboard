"""Reproducible Python-allocation probe; does not claim total RSS or constant disk."""
import argparse
from dataclasses import asdict
import gc
import json
from pathlib import Path
import platform
import sys
import time
import tracemalloc

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from log_analyzer.storage import Store
from log_analyzer.streams import SourceInput
from log_analyzer.limits import ResourceLimits
from log_analyzer.models import ScanOptions


def measure(count,folder):
    if type(count) is not int or not 1<=count<=250000: raise ValueError('count must be an integer from 1 to 250000')
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=True)
    source=folder/f'input-{count}.log'
    if (folder/f'run-{count}.db').exists(): raise FileExistsError('Use a fresh benchmark directory to avoid measuring accumulated run history')
    with source.open('x',encoding='utf-8',newline='') as stream:
        for index in range(count):
            stream.write(f'2026-10-15T12:00:00Z ERROR failure code=E{index} request_id={index:016x}\n')
    store=Store(folder/f'run-{count}.db')
    gc.collect(); tracemalloc.start(); started=time.perf_counter()
    imported=store.import_sources([SourceInput.file(source)])
    view=store.view(imported.source_ids)
    event_page=view.page_events(); group_page=view.page_groups()
    _,peak=tracemalloc.get_traced_memory(); tracemalloc.stop()
    return {'source_sha256':view.sources[0].sha256,'accepted_events':view.event_count,'error_count':view.error_count,'records':count,'unique_groups':view.group_count,'input_bytes':source.stat().st_size,
            'elapsed_seconds':round(time.perf_counter()-started,4),'peak_python_bytes':peak,
            'peak_event_batch':imported.peak_event_batch,'event_page_rows':len(event_page),
            'group_page_rows':len(group_page),'database_bytes':store.path.stat().st_size}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--counts',type=int,nargs='+',default=[2000,20000])
    args=parser.parse_args()
    if any(count<1 or count>250000 for count in args.counts): parser.error('counts must be 1–250000')
    if len(set(args.counts))!=len(args.counts): parser.error('counts must not repeat')
    results=[measure(count,args.directory) for count in args.counts]
    print(json.dumps({'python':platform.python_version(),'platform':platform.platform(),
                      'limits':asdict(ResourceLimits()),'scan_options':asdict(ScanOptions()),'measurement':'tracemalloc during import, SQL selection aggregation, and bounded first pages; fixture creation/schema migration excluded; SQLite native memory and OS RSS not measured',
                      'samples':results},indent=2))

if __name__=='__main__': main()
