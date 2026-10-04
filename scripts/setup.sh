#!/usr/bin/env bash
# One-shot dev setup for Linux/macOS (production target is Windows 11 —
# use scripts/install_windows.ps1 there).
#   ./scripts/setup.sh you@example.com [FRED_API_KEY]
set -euo pipefail
cd "$(dirname "$0")/.."

EMAIL="${1:-}"
FRED="${2:-}"
if [[ -z "$EMAIL" || "$EMAIL" != *@* ]]; then
    echo "usage: $0 you@example.com [FRED_API_KEY]   (real email required by SEC)" >&2
    exit 1
fi

echo "== venv + dependencies"
python3 -m venv .venv 2>/dev/null || true
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt

echo "== .env"
[[ -f .env ]] || cp .env.example .env
sed -i.bak "s|^EDGAR_USER_AGENT=.*|EDGAR_USER_AGENT=DeepValueFund research bot ${EMAIL}|" .env
[[ -n "$FRED" ]] && sed -i.bak "s|^FRED_API_KEY=.*|FRED_API_KEY=${FRED}|" .env
rm -f .env.bak

echo "== offline test suite"
./.venv/bin/python -m unittest discover tests

echo "== live integration validation"
./.venv/bin/python -m hedgefund.main validate || true

echo "== doctor preflight"
./.venv/bin/python -m hedgefund.main doctor || true

echo "Setup done. Next: ./.venv/bin/python -m hedgefund.main once"
