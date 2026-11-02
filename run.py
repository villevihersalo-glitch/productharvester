#!/usr/bin/env python3
"""Start local Product Harvester dashboard. Python 3.10+; standard library only."""
import argparse
from pathlib import Path
from tools.harvester_desktop.server import serve

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    serve(Path(__file__).resolve().parent,port=args.port,open_browser=not args.no_browser)
