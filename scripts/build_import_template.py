"""Write the one-time import template (ADR 0027; ticket onboarding/01).

Usage: uv run python scripts/build_import_template.py

Writes `docs/products/elmich/import-template.xlsx` from the same declaration
(`dw_supply_chain.domain.data_import.SHEETS`) the admin screen's template and
the reader both use; `test_data_import.py` fails when the committed copy's
headers drift from it. Rerun after changing a sheet's columns.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dw_supply_chain.adapters.import_workbook import build_template

OUT = Path(__file__).resolve().parents[1] / "docs" / "products" / "elmich" / "import-template.xlsx"


def main() -> int:
    OUT.write_bytes(build_template())
    print(f"wrote {OUT.relative_to(OUT.parents[3])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
