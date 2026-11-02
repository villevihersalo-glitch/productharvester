#!/usr/bin/env python3
"""Automation-friendly CLI for Codex, Claude Code and scheduled local jobs."""
import argparse,json,shutil,uuid
from pathlib import Path
from tools.harvester_desktop.engine import HarvesterDesktop

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Product Harvester CLI')
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('status')
    exp=commands.add_parser('export');exp.add_argument('--output',default='workspace/review.csv')
    run=commands.add_parser('run');run.add_argument('--source',required=True)
    run.add_argument('--mode',required=True,choices=('file','url_export','sitemap','urls','shopify','alko_file','alko_mirror'))
    run.add_argument('--category',default='');run.add_argument('--url',default='')
    run.add_argument('--urls-file',default='');run.add_argument('--filename',default='');run.add_argument('--file',default='')
    run.add_argument('--pattern',default='');run.add_argument('--max-pages',type=int,default=600)
    run.add_argument('--delay',type=float,default=1.5)
    run.add_argument('--resume',action='store_true',help='Resume cached Alko mirror pages')
    args=parser.parse_args();root=Path(__file__).resolve().parent
    svc=HarvesterDesktop(root)
    if args.command=='status':print(json.dumps(svc.status(),indent=2,ensure_ascii=False))
    elif args.command=='export':
        output=root/args.output
        output.parent.mkdir(parents=True,exist_ok=True)
        print('Exported:',svc.export_csv(output),'rows to',output)
    else:
        options={k:getattr(args,k) for k in ('mode','source','category','url','filename','pattern','max_pages','delay')}
        options['resume']=args.resume
        if args.urls_file:options['urls']=Path(args.urls_file).read_text(encoding='utf-8')
        if args.file:
            chosen=Path(args.file).resolve()
            if not chosen.is_file() or chosen.suffix.lower() not in ('.csv','.json','.xlsx'):
                parser.error('--file must point to a .csv, .json or .xlsx file')
            dest=svc.workspace/'imports'/('cli_'+uuid.uuid4().hex+chosen.suffix.lower())
            shutil.copy2(chosen,dest)
            options['filename']=dest.name
        svc.start(options);svc.worker.join()
        print(json.dumps(svc.status()['job'],indent=2,ensure_ascii=False))
        raise SystemExit(0 if svc.status()['job']['state']=='finished' else 1)
