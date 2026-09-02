from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algorithm_versions.research_pack import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
