#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
CONFIG_PATH="${CONFIG_PATH:-$ROOT_DIR/.local-config}"
MEDIA_ROOT="${MEDIA_ROOT:-$HOME/Desktop}"
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8080}"
FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
BACKEND_LOG="${BACKEND_LOG:-$CONFIG_PATH/dev-api.log}"
FRONTEND_LOG="${FRONTEND_LOG:-$CONFIG_PATH/dev-vite.log}"
BACKEND_PID_FILE="${BACKEND_PID_FILE:-$CONFIG_PATH/dev-api.pid}"
FRONTEND_PID_FILE="${FRONTEND_PID_FILE:-$CONFIG_PATH/dev-vite.pid}"
VENV_PYTHON="$VENV_DIR/bin/python"
VITE_SCRIPT="$ROOT_DIR/frontend/node_modules/vite/bin/vite.js"

mkdir -p "$CONFIG_PATH"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "Python virtualenv not found at $VENV_DIR"
  echo "Create it first, then rerun this script."
  exit 1
fi

if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
  echo "frontend/node_modules is missing."
  echo "Run: npm -C frontend install"
  exit 1
fi

if [[ ! -d "$MEDIA_ROOT" ]]; then
  echo "MEDIA_ROOT does not exist: $MEDIA_ROOT"
  exit 1
fi

source "$VENV_DIR/bin/activate"
export CONFIG_PATH
export MEDIA_ROOT
export BACKEND_HOST BACKEND_PORT
export APP_HOST="$BACKEND_HOST" APP_PORT="$BACKEND_PORT"

access_hosts() {
  local bind_host="$1"
  if [[ "$bind_host" != "0.0.0.0" ]]; then
    printf '%s\n' "$bind_host"
    return
  fi

  printf '%s\n' "127.0.0.1"
  hostname 2>/dev/null || true
  hostname -f 2>/dev/null || true
  if command -v scutil >/dev/null 2>&1; then
    local mac_host
    mac_host="$(scutil --get LocalHostName 2>/dev/null || true)"
    if [[ -n "$mac_host" ]]; then
      printf '%s.local\n' "$mac_host"
    fi
  fi
  if command -v ip >/dev/null 2>&1; then
    ip -o -4 addr show up | awk '{split($4, address, "/"); print address[1]}'
  elif command -v ifconfig >/dev/null 2>&1; then
    ifconfig -a | awk '$1 == "inet" {print $2}'
  fi
}

print_access_urls() {
  local label="$1" bind_host="$2" port="$3" path="$4" address
  if [[ -n "$label" ]]; then
    echo "$label"
  fi
  while IFS= read -r address; do
    [[ -n "$address" ]] || continue
    printf '  http://%s:%s%s\n' "$address" "$port" "$path"
  done < <(access_hosts "$bind_host" | awk '!/^(0\.|169\.254\.)/ && !seen[$0]++')
}

HEALTH_HOST="$BACKEND_HOST"
if [[ "$HEALTH_HOST" == "0.0.0.0" ]]; then
  HEALTH_HOST="127.0.0.1"
fi
FRONTEND_HEALTH_HOST="$FRONTEND_HOST"
if [[ "$FRONTEND_HEALTH_HOST" == "0.0.0.0" ]]; then
  FRONTEND_HEALTH_HOST="127.0.0.1"
fi
FRONTEND_PID=""
TAIL_PID=""
BACKEND_TAIL_PID=""
PROCESS_TREE=()

process_command() {
  ps -p "$1" -o args= 2>/dev/null || true
}

collect_process_descendants() {
  local parent_pid="$1" child_pid
  while IFS= read -r child_pid; do
    [[ -n "$child_pid" ]] || continue
    PROCESS_TREE+=("$child_pid")
    collect_process_descendants "$child_pid"
  done < <(pgrep -P "$parent_pid" 2>/dev/null || true)
}

process_tree_is_alive() {
  local process_pid
  for process_pid in "$@"; do
    if kill -0 "$process_pid" 2>/dev/null; then
      return 0
    fi
  done
  return 1
}

stop_managed_process_tree() {
  local root_pid="$1" command_marker="$2" command_line index
  [[ "$root_pid" =~ ^[0-9]+$ ]] || return 0
  command_line="$(process_command "$root_pid")"
  [[ -n "$command_line" && "$command_line" == *"$command_marker"* ]] || return 0

  PROCESS_TREE=()
  collect_process_descendants "$root_pid"
  kill -TERM "$root_pid" 2>/dev/null || true
  for ((index=${#PROCESS_TREE[@]} - 1; index >= 0; index--)); do
    kill -TERM "${PROCESS_TREE[index]}" 2>/dev/null || true
  done

  for _ in {1..30}; do
    process_tree_is_alive "$root_pid" "${PROCESS_TREE[@]}" || break
    sleep 0.1
  done
  if process_tree_is_alive "$root_pid" "${PROCESS_TREE[@]}"; then
    kill -KILL "$root_pid" 2>/dev/null || true
    for ((index=${#PROCESS_TREE[@]} - 1; index >= 0; index--)); do
      kill -KILL "${PROCESS_TREE[index]}" 2>/dev/null || true
    done
  fi
}

backend_listener_matches() {
  local listener_pid="$1" command_line cwd
  command_line="$(process_command "$listener_pid")"
  if [[ "$command_line" == *"$VENV_PYTHON -m uvicorn backend.app.main:app"* ]]; then
    return 0
  fi

  # A reload worker can outlive its manager. Only treat it as ours when it is
  # a Python multiprocessing child whose working directory is this checkout.
  [[ "$command_line" == *multiprocessing.spawn* || "$command_line" == *spawn_main* ]] || return 1
  if [[ -e "/proc/$listener_pid/cwd" ]]; then
    cwd="$(readlink "/proc/$listener_pid/cwd" 2>/dev/null || true)"
    [[ "$cwd" == "$ROOT_DIR" ]]
    return
  fi
  if command -v lsof >/dev/null 2>&1; then
    cwd="$(lsof -a -p "$listener_pid" -d cwd -Fn 2>/dev/null | awk 'substr($0,1,1)=="n" {print substr($0,2); exit}')"
    [[ "$cwd" == "$ROOT_DIR" ]]
    return
  fi
  return 1
}

stop_frontend() {
  if [[ -f "$FRONTEND_PID_FILE" ]]; then
    local existing_pid
    existing_pid="$(cat "$FRONTEND_PID_FILE" 2>/dev/null || true)"
    stop_managed_process_tree "$existing_pid" "$VITE_SCRIPT"
    rm -f "$FRONTEND_PID_FILE"
  fi

  local port_pids pid command_line
  if command -v lsof >/dev/null 2>&1; then
    port_pids="$(lsof -nP -tiTCP:"$FRONTEND_PORT" -sTCP:LISTEN 2>/dev/null || true)"
    while IFS= read -r pid; do
      [[ -n "$pid" ]] || continue
      if [[ "$(process_command "$pid")" == *"$VITE_SCRIPT"* ]]; then
        stop_managed_process_tree "$pid" "$VITE_SCRIPT"
      else
        echo "Frontend port $FRONTEND_PORT is occupied by unrelated process $pid; no process was stopped." >&2
        return 1
      fi
    done <<< "$port_pids"
  fi
}

stop_backend() {
  if [[ -f "$BACKEND_PID_FILE" ]]; then
    local existing_pid
    existing_pid="$(cat "$BACKEND_PID_FILE" 2>/dev/null || true)"
    stop_managed_process_tree "$existing_pid" "uvicorn backend.app.main:app"
    rm -f "$BACKEND_PID_FILE"
  fi

  local port_pids pid command_line
  if command -v lsof >/dev/null 2>&1; then
    port_pids="$(lsof -nP -tiTCP:"$BACKEND_PORT" -sTCP:LISTEN 2>/dev/null || true)"
    while IFS= read -r pid; do
      [[ -n "$pid" ]] || continue
      if backend_listener_matches "$pid"; then
        command_line="$(process_command "$pid")"
        if [[ "$command_line" == *"$VENV_PYTHON -m uvicorn backend.app.main:app"* ]]; then
          stop_managed_process_tree "$pid" "$VENV_PYTHON -m uvicorn backend.app.main:app"
        else
          stop_managed_process_tree "$pid" "spawn_main"
        fi
      else
        echo "Backend port $BACKEND_PORT is occupied by unrelated process $pid; no process was stopped." >&2
        return 1
      fi
    done <<< "$port_pids"
  fi
}

cleanup() {
  if [[ -n "$TAIL_PID" ]]; then
    kill -TERM "$TAIL_PID" 2>/dev/null || true
  fi
  if [[ -n "$BACKEND_TAIL_PID" ]]; then
    kill -TERM "$BACKEND_TAIL_PID" 2>/dev/null || true
  fi
  if [[ -n "$FRONTEND_PID" ]]; then
    stop_managed_process_tree "$FRONTEND_PID" "$VITE_SCRIPT"
  fi
  stop_frontend || true
  stop_backend || true
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

stop_frontend
stop_backend

cd "$ROOT_DIR"
nohup "$VENV_PYTHON" -m uvicorn backend.app.main:app --reload --host "$BACKEND_HOST" --port "$BACKEND_PORT" --log-level error --no-access-log \
  >"$BACKEND_LOG" 2>&1 &
API_PID=$!
echo "$API_PID" > "$BACKEND_PID_FILE"

backend_ready=0
for _ in {1..80}; do
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo "Backend exited during startup. Recent log output:"
    tail -n 80 "$BACKEND_LOG" || true
    exit 1
  fi

  if curl -fsS "http://$HEALTH_HOST:$BACKEND_PORT/api/health" >/dev/null 2>&1; then
    backend_ready=1
    break
  fi
  sleep 0.25
done

if [[ "$backend_ready" -ne 1 ]]; then
  echo "Backend did not become healthy in time. Recent log output:"
  tail -n 80 "$BACKEND_LOG" || true
  exit 1
fi

cd "$ROOT_DIR/frontend"
node "$VITE_SCRIPT" --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" --strictPort --logLevel error \
  >"$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!
echo "$FRONTEND_PID" > "$FRONTEND_PID_FILE"

frontend_ready=0
for _ in {1..80}; do
  if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    echo "Frontend exited during startup. Recent log output:"
    tail -n 80 "$FRONTEND_LOG" || true
    exit 1
  fi
  if curl -fsS "http://$FRONTEND_HEALTH_HOST:$FRONTEND_PORT/" >/dev/null 2>&1; then
    frontend_ready=1
    break
  fi
  sleep 0.25
done

if [[ "$frontend_ready" -ne 1 ]]; then
  echo "Frontend did not become ready in time. Recent log output:"
  tail -n 80 "$FRONTEND_LOG" || true
  exit 1
fi

echo "Everything is ready. Open one of these URLs:"
print_access_urls "" "$FRONTEND_HOST" "$FRONTEND_PORT" "/"
tail -n +1 -f "$FRONTEND_LOG" &
TAIL_PID=$!
tail -n +1 -f "$BACKEND_LOG" &
BACKEND_TAIL_PID=$!

if wait "$FRONTEND_PID"; then
  exit 0
else
  frontend_exit_code=$?
  if [[ "$frontend_exit_code" -ne 130 && "$frontend_exit_code" -ne 143 ]]; then
    echo "Frontend exited with code $frontend_exit_code. Recent log output:"
    tail -n 80 "$FRONTEND_LOG" || true
  fi
  exit "$frontend_exit_code"
fi
