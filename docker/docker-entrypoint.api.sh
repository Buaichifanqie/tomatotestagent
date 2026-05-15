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
        echo "Usage: docker run --rm testagent-api-runner [testagent|python3] [args...]"
        echo ""
        echo "Default: testagent run --skill api_smoke_test"
        echo ""
        echo "Environment variables:"
        echo "  TESTAGENT_API_BASE_URL   Base URL for API tests (default: http://localhost:8000)"
        echo "  TESTAGENT_API_TIMEOUT    Request timeout in seconds (default: 30)"
        echo ""
        exec testagent "$@"
        ;;
esac
