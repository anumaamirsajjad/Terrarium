"""The intervention search agent: run the simulator in a loop and report what won.

Layer 3, beside `api/` and `dsl/`. It composes them — it owns no physics, no grid and no
geometry of its own. `dsl.validate.resolve`'s refusal is the loop's feedback signal, which
is the most interesting thing about this package: the validator built as a product feature
turns out to be a usable reward signal with no extra work.

See D25 (where a model may be reached) and D26 (how geometry gets proposed without one).
"""
