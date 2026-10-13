#!/usr/bin/env bash
# The 60-second demo: "never" rules in AGENTS.md, backed by tests, with their status.
# Runs in a scratch copy, so this directory stays as it is.  PAUSE=0 ./demo.sh to run straight through.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)/shop"
cp -r "$here" "$work" && cd "$work" && rm -rf .specdeck
pause() { [ "${PAUSE:-1}" = 0 ] || read -r -p $'\n[enter] ' _; }
step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
quiet_pytest() { python3 -m pytest -q -p no:cacheprovider -p no:asyncio "$@" 2>&1 | grep -v -e '^$' -e Warning -e warnings.warn || true; }

step "1. The rules are requirements: a statement, the reason, and a type"
sed -n '/^requirements:/,$p' requirements.yaml | head -9
pause

step "2. Each rule has a test that fails when the rule is broken"
grep -n -A3 '@scenario("rule-decimal-money"' tests/test_rules.py
pause

step "3. Run the tests, then write the rules into AGENTS.md"
quiet_pytest | tail -2
python3 -m specdeck agents
sed -n '/### Design constraints/,/### Definition/p' AGENTS.md | head -4
pause

step "4. An agent adds a refund function, the quick way"
cat agent_change/refund.py | tail -3
cat agent_change/refund.py >> src/shop/orders.py
pause

step "5. The suite catches the broken rule"
quiet_pytest | grep -e FAILED -e "requirements:" -e "passed"
pause

step "6. AGENTS.md now says which rule is broken, why it exists, and which test to make pass"
python3 -m specdeck agents
grep 'Broken in the last run:' AGENTS.md
grep -B2 -A0 '\*\*broken in the last run\*\*' AGENTS.md
