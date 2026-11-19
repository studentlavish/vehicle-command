#!/usr/bin/env bash
# macOS/Linux launcher for the camera agent.  ./start_agent.sh --test runs the self-test.
cd "$(dirname "$0")"
exec .venv/bin/python agent.py "$@"
