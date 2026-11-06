#!/usr/bin/env bash
# Start or stop everything the local demo needs, with one command.
#
#   make demo start [oracle]   scripts/demo.sh start [oracle] [no-open]
#   make demo stop             scripts/demo.sh stop
#   make demo status           scripts/demo.sh status
#
# Options are plain words, because make would take --oracle as one of its own.
#
# The services, in the order they depend on each other: Postgres (Docker), the MCP
# server (127.0.0.1:8765), the agent API (127.0.0.1:8766, with the real model or the
# oracle) and the console (http://localhost:5173). Logs and process ids go to
# var/demo/, so the terminal stays quiet in front of an audience.
#
# Secrets stay in .env: this script never reads it. The services read it themselves,
# and the start-up check prints only whether each secret is set.
set -euo pipefail

ROOT="${SR_AGENT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"
RUN_DIR="var/demo"
LOGS="$RUN_DIR/logs"
PIDS="$RUN_DIR/pids"

MCP_URL="http://127.0.0.1:8765/healthz"
API_URL="http://127.0.0.1:8766/healthz"
WEB_URL="http://localhost:5173"

say() { printf '%s\n' "$*"; }
fail() {
  printf 'demo: %s\n' "$*" >&2
  exit 1
}

healthy() { curl -sf -o /dev/null --max-time 2 "$1"; }

port_pid() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -n 1 || true; }

command_of() { ps -o command= -p "$1" 2>/dev/null || true; }

# A process on one of our ports that this project started, here or by hand.
ours() {
  case "$(command_of "$1")" in
    *sr-mcp* | *"sra serve"* | *serve-oracle* | *vite*) return 0 ;;
    *) return 1 ;;
  esac
}

api_mode() {
  local pid
  pid="$(port_pid 8766)"
  if [ -z "$pid" ]; then
    echo "-"
  elif [[ "$(command_of "$pid")" == *serve-oracle* ]]; then
    echo "oracle"
  else
    echo "real model"
  fi
}

docker_running() { docker info >/dev/null 2>&1; }

postgres_up() { [ -n "$(docker compose ps --status running -q postgres 2>/dev/null)" ]; }

# Start one service in its own process group and wait until its health check passes.
start_service() {
  local name="$1" port="$2" url="$3"
  shift 3
  local pid
  pid="$(port_pid "$port")"
  if [ -n "$pid" ]; then
    # Reuse only our own server that answers; any other program on the port is named.
    if ! ours "$pid"; then
      fail "port $port is taken by '$(command_of "$pid")' (pid $pid); stop it, then run this again"
    fi
    if healthy "$url"; then
      say "  $name: already running"
      return 0
    fi
    fail "the $name on port $port is not answering: make demo stop, then run this again"
  fi
  : >"$LOGS/$name.log"
  set -m
  # No terminal input: Vite reads its keyboard shortcuts from a terminal, and a
  # background process that reads one dies with EIO once the script has returned.
  nohup "$@" </dev/null >>"$LOGS/$name.log" 2>&1 &
  local child=$!
  set +m
  echo "$child" >"$PIDS/$name.pid"
  local _
  for _ in $(seq 1 90); do
    if healthy "$url"; then
      say "  $name: up"
      return 0
    fi
    if ! kill -0 "$child" 2>/dev/null; then
      break
    fi
    sleep 1
  done
  say "demo: $name did not start. The end of $LOGS/$name.log:" >&2
  tail -n 15 "$LOGS/$name.log" >&2
  exit 1
}

# Stop one service: the process group we started, then anything of ours on its port.
stop_service() {
  local name="$1" port="$2" pidfile="$PIDS/$1.pid" pid
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile")"
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    rm -f "$pidfile"
  fi
  local _
  for _ in $(seq 1 10); do
    pid="$(port_pid "$port")"
    if [ -z "$pid" ]; then
      say "  $name: stopped"
      return 0
    fi
    if ! ours "$pid"; then
      say "  $name: port $port is used by '$(command_of "$pid")', left alone"
      return 0
    fi
    kill -TERM "$pid" 2>/dev/null || true
    sleep 1
  done
  pid="$(port_pid "$port")"
  if [ -n "$pid" ] && ours "$pid"; then
    kill -KILL "$pid" 2>/dev/null || true
  fi
  say "  $name: stopped"
}

# Fail fast, with the fix, before anything starts.
preflight() {
  local mode="$1"
  docker_running || fail "Docker is not running: start Docker Desktop, then run this again"
  [ -f .env ] || fail "there is no .env: cp .env.example .env, then fill it in (docs/demo/README.md)"
  [ -d packages/web/node_modules ] || fail "the console's packages are missing: make web-install"
  [ -f var/sim/labels.json ] || fail "there is no simulated world yet: make seed && make ingest"
  # The settings read .env themselves; this prints only whether each secret is set.
  local check
  check="$(uv run --quiet python -c '
try:
    from sr_agent.settings import AgentSettings
    s = AgentSettings()
    print(int(s.mcp_token is not None), int(s.anthropic_api_key is not None))
except Exception as error:
    print("error", type(error).__name__)
' 2>/dev/null || echo "error uv")"
  case "$check" in
    error*) fail "the agent's settings did not load (${check#error }): check .env against .env.example" ;;
  esac
  [ "${check%% *}" = "1" ] || fail "MCP_HTTP_TOKEN is not set in .env (32 or more random characters)"
  if [ "$mode" = "real" ] && [ "${check##* }" != "1" ]; then
    fail "ANTHROPIC_API_KEY is not set in .env; or run make demo start oracle, which needs no key"
  fi
}

start() {
  local mode="real" open_browser="yes" arg
  for arg in "$@"; do
    case "$arg" in
      oracle | --oracle) mode="oracle" ;;
      no-open | --no-open) open_browser="no" ;;
      *) fail "unknown option '$arg': make demo start, or make demo start oracle" ;;
    esac
  done
  mkdir -p "$LOGS" "$PIDS"
  preflight "$mode"

  if [ "$mode" = "oracle" ]; then
    say "Starting the demo with the oracle in place of the model (no API key, no cost):"
  else
    say "Starting the demo with the real model:"
  fi
  if postgres_up; then
    say "  postgres: already running"
  else
    docker compose up -d --wait postgres >"$LOGS/postgres.log" 2>&1 \
      || fail "Postgres did not start: see $LOGS/postgres.log"
    say "  postgres: up"
  fi
  start_service mcp 8765 "$MCP_URL" uv run sr-mcp --transport http
  # Always a fresh agent API: the history starts empty, and the mode is the one asked for.
  stop_service api 8766 >/dev/null
  if [ "$mode" = "oracle" ]; then
    start_service api 8766 "$API_URL" uv run sr-evals serve-oracle
  else
    start_service api 8766 "$API_URL" uv run sra serve
  fi
  start_service web 5173 "$WEB_URL" pnpm --dir packages/web dev

  say ""
  say "Ready: open $WEB_URL"
  say "Logs are in $LOGS/. Stop everything with: make demo stop"
  if [ "$open_browser" = "yes" ] && command -v open >/dev/null 2>&1; then
    open "$WEB_URL"
  fi
}

stop() {
  say "Stopping the demo:"
  stop_service web 5173
  stop_service api 8766
  stop_service mcp 8765
  if docker_running && postgres_up; then
    docker compose stop postgres >/dev/null 2>&1 || true
    say "  postgres: stopped (its data is kept)"
  else
    say "  postgres: not running"
  fi
}

status() {
  local pg="down" mcp="down" api="down" web="down"
  if docker_running && postgres_up; then pg="up"; fi
  if healthy "$MCP_URL"; then mcp="up"; fi
  if healthy "$API_URL"; then api="up ($(api_mode))"; fi
  if healthy "$WEB_URL"; then web="up"; fi
  say "postgres    $pg"
  say "mcp server  $mcp   http://127.0.0.1:8765"
  say "agent api   $api   http://127.0.0.1:8766"
  say "console     $web   $WEB_URL"
}

case "${1:-}" in
  start)
    shift
    start "$@"
    ;;
  stop) stop ;;
  status) status ;;
  *) fail "usage: make demo start [oracle] | make demo stop | make demo status" ;;
esac
