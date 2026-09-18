import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from seek_job.cli import main
raise SystemExit(main(["validate-handoff", *sys.argv[1:]]))
