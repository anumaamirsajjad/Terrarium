"""Layer 2 - Physics Cores: PURE simulators.

A core is `core(baseline_cube, intervention) -> result_cube` and nothing else:

    no file reads      no network calls     no database access
    no global state    no clock reads       no config lookups

Everything a core needs arrives as an argument. This purity is what makes the physics
testable offline, cacheable, and swappable. See CLAUDE.md > Coding conventions.
"""
