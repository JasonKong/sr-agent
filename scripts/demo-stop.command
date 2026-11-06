#!/usr/bin/env bash
# Double-click in Finder to stop everything the demo started (the same as make demo-stop).
cd "$(dirname "$0")/.." || exit 1
scripts/demo.sh stop
