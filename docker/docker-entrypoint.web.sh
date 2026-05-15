#!/bin/bash
set -e

if [ "${1#-}" != "$1" ]; then
    set -- testagent "$@"
fi

case "$1" in
    testagent|python3|python)
        exec "$@"
        ;;
    *)
        echo "Usage: docker run --rm testagent-web-runner [testagent|python3] [args...]"
        echo ""
        echo "Default: testagent run --skill web_smoke_test"
        echo ""
        echo "Environment variables:"
    echo "  TESTAGENT_WEB_BASE_URL   Base URL for Web tests (default: http://localhost:3000)"
        echo "  TESTAGENT_WEB_TIMEOUT    Page timeout in seconds (default: 30)"
        echo ""
        exec testagent "$@"
        ;;
esac
