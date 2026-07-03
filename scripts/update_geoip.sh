#!/usr/bin/env bash
# Download the latest DB-IP IP-to-City Lite database (free, CC-BY, no license key)
# and atomically install it at data/dbip-city-lite.mmdb. Run monthly via cron.
#
# DB-IP publishes a new file each month at:
#   https://download.db-ip.com/free/dbip-city-lite-YYYY-MM.mmdb.gz
# We try the current month, falling back to the previous month early in the month
# before the new file is published.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/data/dbip-city-lite.mmdb"
mkdir -p "$ROOT/data"

try_month() {
    local ym="$1"
    local url="https://download.db-ip.com/free/dbip-city-lite-${ym}.mmdb.gz"
    local tmp
    tmp="$(mktemp)"
    echo "trying $url"
    if curl -fsSL --max-time 120 "$url" -o "$tmp.gz" && gunzip -c "$tmp.gz" > "$tmp"; then
        mv -f "$tmp" "$DEST"
        rm -f "$tmp.gz"
        echo "installed $DEST ($(du -h "$DEST" | cut -f1))"
        return 0
    fi
    rm -f "$tmp" "$tmp.gz"
    return 1
}

CUR="$(date -u +%Y-%m)"
PREV="$(date -u -d 'last month' +%Y-%m 2>/dev/null || date -u -v-1m +%Y-%m)"

if try_month "$CUR" || try_month "$PREV"; then
    exit 0
fi
echo "ERROR: could not download DB-IP City Lite for $CUR or $PREV" >&2
exit 1
