#!/usr/bin/env bash
# Create a self-hosted rule registry at DIR (a git repository) and publish the seed rules into it.
#   ./make-registry.sh ~/rules-registry
set -euo pipefail
dest="${1:?usage: make-registry.sh DIR}"
here="$(cd "$(dirname "$0")" && pwd)"
python3 -m specdeck rules init "$dest"
work="$(mktemp -d)"
cp "$here/seed-rules.yaml" "$work/requirements.yaml"
cd "$work"
for id in $(python3 -c "import yaml; print(' '.join(r['id'] for r in yaml.safe_load(open('requirements.yaml'))['requirements']))"); do
  python3 -m specdeck rules publish "$id" --registry "$dest"
done
git -C "$dest" log --oneline | head -20
