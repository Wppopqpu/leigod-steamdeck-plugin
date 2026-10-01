#!/bin/sh
# Compatibility entry point; the old fd/IP-based strace classifier was unreliable.
exec python3 "$(dirname "$0")/debug-binding-api.py" --seconds "${LEIGOD_DEBUG_DURATION:-45}" "$@"
