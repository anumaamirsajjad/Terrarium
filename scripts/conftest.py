"""Make `scripts/` importable by its own tests.

The scripts are entrypoints rather than a package — they have no `__init__.py`, and giving
them one would put them in the built wheel, which is the opposite of what they are for. So
the directory goes on `sys.path` here instead, and each test imports the module by name.

**What these tests are for.** `scripts/` is the operational surface: the two builders
produce every artefact the rest of the project reads, and until 2026-08-07 none of it was
covered. Three real bugs shipped in that gap and were found by running the code against
live services rather than by any test:

- `validate_air.py` died on the first of seven Lahore sensors that answer HTTP 500,
  ingested OpenAQ's `-999` no-data sentinel as a concentration, and compared each station's
  *latest* reading regardless of when it was taken — mixing a winter smog episode at one
  monitor with last night at another.
- `build_air_layers.py` resolved cube windows against `settings.window_years` rather than
  against the cube, so on any cube built with `--years` it matched nothing and then
  **overwrote a populated `wind_direction_deg` with NaN**, in place.

All four are pinned below. The pattern in every case is the same: the pure logic — what to
keep, what to skip, what to reduce — is tested directly, and the HTTP boundary is stubbed.
No test here opens a socket.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
