#!/usr/bin/env bash
# arkiv — Smoke Test
# Usage: bash smoke-test.sh [--platform pc|docker]
#
# Auto-detects platform:
#   pc     — test against local server (localhost:8501)
#   docker — test against Docker container
set -e

RED='\033[31m'; GREEN='\033[32m'; NC='\033[0m'
PASS=0; FAIL=0

# Parse --platform
PLATFORM=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --platform) PLATFORM="$2"; shift 2 ;;
        --platform=*) PLATFORM="${1#*=}"; shift ;;
        *) shift ;;
    esac
done

# Auto-detect
if [ -z "$PLATFORM" ]; then
    if [ -f "/.dockerenv" ] || [ -n "$ARKIV_DOCKER" ]; then
        PLATFORM="docker"
    else
        PLATFORM="pc"
    fi
fi

PORT=${ARKIV_PORT:-8501}
if [ "$PLATFORM" = "docker" ]; then
    BASE="http://localhost:$PORT"
else
    BASE="http://localhost:$PORT"
fi

check() {
    local name="$1" ok="$2" detail="$3"
    if [ "$ok" = "true" ]; then
        PASS=$((PASS+1)); echo -e "  ${GREEN}✓${NC} $name $detail"
    else
        FAIL=$((FAIL+1)); echo -e "  ${RED}✗${NC} $name $detail"
    fi
}

echo "═══ arkiv Smoke Test ($PLATFORM) ═══"
echo ""

# 1. Health check (platform-aware)
echo "── Environment ──"
# Find the right Python: venv > python > python3
if [ -f ".venv/Scripts/python.exe" ]; then
    PYTHON=".venv/Scripts/python.exe"
elif [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif python -c "import fastapi" 2>/dev/null; then
    PYTHON="python"
else
    PYTHON="python3"
fi
$PYTHON health.py --platform "$PLATFORM" || true
echo ""

# 2. Server running?
echo "── Server ──"
HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$BASE" 2>/dev/null || echo "000")
check "Server reachable" "$([ "$HTTP" = "200" ] && echo true || echo false)" "HTTP $HTTP"

# 3. API endpoints
echo "── API Endpoints ──"
for ep in "/api/media?limit=1" "/api/stats" "/api/tags" "/api/duration-by-lang" "/api/size-by-ext"; do
    HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$BASE$ep" 2>/dev/null || echo "000")
    check "GET $ep" "$([ "$HTTP" = "200" ] && echo true || echo false)" "HTTP $HTTP"
done

# 4. Media count
echo "── Data ──"
COUNT=$(curl -s --max-time 5 "$BASE/api/stats" 2>/dev/null | $PYTHON -c "import sys,json; print(json.load(sys.stdin).get('total',0))" 2>/dev/null || echo "0")
if [ "$PLATFORM" = "docker" ] && [ "$COUNT" = "0" ]; then
    check "Media files indexed" "true" "0 files (fresh Docker — ingest media to populate)"
else
    check "Media files indexed" "$([ "$COUNT" -gt "0" ] && echo true || echo false)" "$COUNT files"
fi

# 5. Search
echo "── Search ──"
SEARCH_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$BASE/api/media?q=test&limit=1" 2>/dev/null || echo "000")
check "Semantic search" "$([ "$SEARCH_HTTP" = "200" ] && echo true || echo false)" "HTTP $SEARCH_HTTP"

# 6. Static files
echo "── Static ──"
INDEX_SIZE=$(curl -s --max-time 5 "$BASE" 2>/dev/null | wc -c | tr -d ' ')
check "index.html served" "$([ "$INDEX_SIZE" -gt "1000" ] && echo true || echo false)" "${INDEX_SIZE} bytes"

echo ""
echo "═══ Result: $PASS PASS, $FAIL FAIL ($PLATFORM) ═══"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
