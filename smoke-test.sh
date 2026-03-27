#!/usr/bin/env bash
# arkiv — Full Smoke Test
# Usage: bash smoke-test.sh
set -e

RED='\033[31m'; GREEN='\033[32m'; YELLOW='\033[33m'; NC='\033[0m'
PASS=0; FAIL=0; SKIP=0
PORT=${ARKIV_PORT:-8501}
BASE="http://localhost:$PORT"

check() {
    local name="$1" ok="$2" detail="$3"
    if [ "$ok" = "true" ]; then
        PASS=$((PASS+1)); echo -e "  ${GREEN}✓${NC} $name $detail"
    else
        FAIL=$((FAIL+1)); echo -e "  ${RED}✗${NC} $name $detail"
    fi
}

echo "═══ arkiv Smoke Test ═══"
echo ""

# 1. Health check
echo "── Environment ──"
python3 health.py 2>/dev/null
echo ""

# 2. Server running?
echo "── Server ──"
HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "$BASE" 2>/dev/null || echo "000")
check "Server reachable" "$([ "$HTTP" = "200" ] && echo true || echo false)" "HTTP $HTTP"

# 3. API endpoints
echo "── API Endpoints ──"
for ep in "/api/media?limit=1" "/api/stats" "/api/tags" "/api/duration-by-lang" "/api/size-by-ext"; do
    HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "$BASE$ep" 2>/dev/null || echo "000")
    check "GET $ep" "$([ "$HTTP" = "200" ] && echo true || echo false)" "HTTP $HTTP"
done

# 4. Media count
echo "── Data ──"
COUNT=$(curl -s --max-time 3 "$BASE/api/stats" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('total',0))" 2>/dev/null || echo "0")
check "Media files indexed" "$([ "$COUNT" -gt "0" ] && echo true || echo false)" "$COUNT files"

# 5. Search
echo "── Search ──"
SEARCH_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$BASE/api/media?q=test&limit=1" 2>/dev/null || echo "000")
check "Semantic search" "$([ "$SEARCH_HTTP" = "200" ] && echo true || echo false)" "HTTP $SEARCH_HTTP"

# 6. Static files
echo "── Static ──"
INDEX_SIZE=$(curl -s --max-time 3 "$BASE" 2>/dev/null | wc -c | tr -d ' ')
check "index.html served" "$([ "$INDEX_SIZE" -gt "1000" ] && echo true || echo false)" "${INDEX_SIZE} bytes"

echo ""
echo "═══ Result: $PASS PASS, $FAIL FAIL ═══"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
