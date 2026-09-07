"""Resumable controller for GitHub-hosted or standalone home-PC downloads."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--init',default='2026090612')
    p.add_argument('--targets',default='202612,202701,202702,202703')
    p.add_argument('--years',default=','.join(map(str,range(2011,2026)))+',forecast')
    p.add_argument('--rolling-days',type=int,choices=range(1,7),default=6)
    p.add_argument('--exclude-cycles',default='2026090418')
    p.add_argument('--checkpoint-dir',type=Path,required=True)
    p.add_argument('--task-timeout-minutes',type=int,default=240)
    p.add_argument('--plan',action='store_true')
    a=p.parse_args()
    if a.task_timeout_minutes<1:p.error('timeout must be positive')
    years=a.years.split(',');targets=a.targets.split(',')
    if any(y!='forecast' and y not in list(map(str,range(2011,2026))) for y in years):p.error('invalid year')
    root=a.checkpoint_dir.resolve();root.mkdir(parents=True,exist_ok=True)
    # One invocation at a time per checkpoint directory on Linux/WSL.
    import fcntl
    with (root/'campaign.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('Another campaign uses this checkpoint directory')
        tasks=[]
        for year in years:
            for target in targets:
                command=[sys.executable,str(Path(__file__).with_name('cfsv2_surface_phase_build.py')),
                         '--init',a.init,'--targets',target,'--rolling-days',str(a.rolling_days),
                         '--exclude-cycles',a.exclude_cycles,'--workers','1','--month-pause-seconds','60',
                         '--raw-cache',str(root/'raw'),'--bundles',str(root/'months')]
                if year!='forecast':command+=['--historical-year',year]
                tasks.append((year,target,command))
        if a.plan:
            print(json.dumps([{'year':y,'target':t,'command':c} for y,t,c in tasks],indent=2));return
        results=[]
        def save():
            temporary=root/'campaign-status.tmp'
            temporary.write_text(json.dumps({'init':a.init,'targets':targets,'years':years,
                'excluded_cycles':a.exclude_cycles,'rolling_days':a.rolling_days,
                'expected_tasks':len(tasks),'results':results,'updated_unix':time.time()},indent=2))
            temporary.replace(root/'campaign-status.json')
        for year,target,command in tasks:
            log=root/f'{year}-{target}.log'
            print(f'Starting {year}/{target}; log: {log}',flush=True)
            with log.open('a') as stream:
                try:
                    result=subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,timeout=a.task_timeout_minutes*60)
                    state='complete' if result.returncode==0 else 'incomplete'
                except subprocess.TimeoutExpired:state='timeout'
            results.append({'year':year,'target':target,'state':state,'log':str(log)})
            report=root/'months'/'acquisition-status.json'
            if report.exists():
                (root/f'{year}-{target}-acquisition.json').write_bytes(report.read_bytes())
            save();print(f'{year}/{target}: {state}',flush=True)
        if any(r['state']!='complete' for r in results):raise SystemExit('Campaign incomplete; checkpoints retained. See campaign-status.json')

if __name__=='__main__':main()
