#!/bin/bash
# Per-segment audit gate. Usage: gate.sh <segN> <#/route> <pngname>
# Tier-A: forbidden-css + build(exit0) + boot + 0 console error. Saves PNG for morning.
set -e
cd ~/Projects/arkiv/frontend
echo "=== forbidden-css ==="
node scripts/audit/forbidden-css.mjs src
echo "=== build ==="
npm run build >"/tmp/arkiv-$1-build.log" 2>&1 && echo "build OK ($(grep -ic warn "/tmp/arkiv-$1-build.log") warn)" || { echo "BUILD FAIL"; tail -20 "/tmp/arkiv-$1-build.log"; exit 1; }
pkill -f "vite preview" 2>/dev/null || true
sleep 1
rm -f /tmp/arkiv-preview.log
npm run preview >/tmp/arkiv-preview.log 2>&1 &
until grep -q "http://localhost" /tmp/arkiv-preview.log 2>/dev/null; do sleep 1; done
URL=$(grep -oE "http://localhost:[0-9]+/" /tmp/arkiv-preview.log | head -1)
echo "=== shoot ${URL}$2 ==="
node scripts/audit/shoot.mjs "${URL}$2" ".audit/$1/$3.png" ".audit/$1/console.json"
