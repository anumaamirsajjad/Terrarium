"""Real published policy, turned into plans this tile can actually run.

*"Here is what the city's own published plan would deliver on this tile"* is what turns a
sandbox into an instrument. The Punjab Clean Air Action Plan carries a Lahore PM2.5 source
apportionment — **diesel 28 % + two-stroke exhaust 8 % = 36 %** — which maps straight onto
`emission_fraction_removed` and becomes a preset with a government citation behind it,
rather than the literature figures `dsl/library.py` has to disclaim.

Two rules, and the second is the one that matters:

- **A quote must be findable in the document**, after both sides are de-spaced. A measure
  whose quote does not match is dropped, whatever else it says.
- **Report the miss rate.** Most measures — fuel sulfur limits, catalytic converters, CNG
  policy — cannot be expressed as a canopy fraction or an emission fraction. The output
  says *"N measures extracted, M expressible on this tile"* rather than silently keeping
  the M. That gap is a finding about the scope of the two levers, and burying it would be
  the same mistake as quoting a cooling figure without its hindcast correction.
"""
