set -e
cd "$(dirname "$0")"
python -u run.py --arms speconly --runs 3 --budget 2000000
python -u run.py --arms mono --runs 4 --budget 2000000
