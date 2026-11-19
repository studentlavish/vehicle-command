#!/usr/bin/env bash
# Kills anything left on the app ports and (optionally) stops MongoDB.
for port in 8001 3000; do
  pid=$(lsof -ti tcp:$port 2>/dev/null); [ -n "$pid" ] && kill $pid && echo "stopped :$port"
done
[ "${1:-}" = "--mongo" ] && brew services stop mongodb-community
exit 0
