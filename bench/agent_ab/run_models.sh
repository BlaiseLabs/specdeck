set -e
cd "$(dirname "$0")"
AB_MODEL=gpt-4o-mini   python run.py --arms brief,wiring --tasks impact,triage --budget 175000
AB_MODEL=gpt-5.4-mini  python run.py --arms brief,wiring --tasks impact,triage --budget 165000
AB_MODEL=gpt-5-mini    python run.py --arms brief,wiring --tasks impact,triage --budget 235000
