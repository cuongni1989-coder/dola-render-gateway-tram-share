#!/bin/bash
set -euo pipefail
umask 077

vnc_password=${DOLA_VNC_PASSWORD:-}
if [[ ${DOLA_ENV:-} != production || ${#vnc_password} -ne 8 ]]; then
  echo 'Production environment and an 8-character private browser password are required.' >&2
  exit 1
fi

cleanup() {
  trap - EXIT TERM INT
  # Keep the display alive while the API closes browser contexts and saves task state.
  if [[ -n ${api_pid:-} ]]; then
    kill "$api_pid" 2>/dev/null || true
    wait "$api_pid" 2>/dev/null || true
  fi
  kill "${vnc_pid:-}" "${web_pid:-}" "${wm_pid:-}" "${display_pid:-}" 2>/dev/null || true
  wait || true
}
trap cleanup EXIT
trap 'exit 0' TERM INT

Xvfb "$DISPLAY" -screen 0 1440x1000x24 -nolisten tcp &
display_pid=$!
for attempt in {1..50}; do
  if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then break; fi
  sleep 0.1
done
xdpyinfo -display "$DISPLAY" >/dev/null
openbox >/dev/null 2>&1 &
wm_pid=$!
x11vnc -storepasswd "$DOLA_VNC_PASSWORD" /tmp/vnc-password >/dev/null 2>&1
x11vnc -display "$DISPLAY" -localhost -rfbport 5900 -rfbauth /tmp/vnc-password -forever -shared >/dev/null 2>&1 &
vnc_pid=$!
websockify --web /usr/share/novnc 0.0.0.0:6080 localhost:5900 >/dev/null 2>&1 &
web_pid=$!
# One worker only: the task scheduler and account locks live in this process.
python -m uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log &
api_pid=$!
set +e
wait -n "$api_pid" "$vnc_pid" "$web_pid" "$wm_pid" "$display_pid"
status=$?
exit "$((status == 0 ? 1 : status))"
