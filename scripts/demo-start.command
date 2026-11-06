#!/usr/bin/env bash
# Double-click in Finder to start the demo with the real model (the same as make demo).
cd "$(dirname "$0")/.." || exit 1
scripts/demo.sh start
