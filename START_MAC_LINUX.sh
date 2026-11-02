#!/usr/bin/env sh
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then python3 run.py; else echo 'Python 3 is required'; exit 1; fi
