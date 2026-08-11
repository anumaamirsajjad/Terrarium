"""Ask the repository's own record, with citations checked to exist.

Layer 3. The corpus is this project's markdown — the plan, the audit, the decisions
register, `CLAUDE.md`, the user guide — which is an unusually good corpus for one reason:
**this project writes down what it does not know.** "Why is the cooling divided by 2.5?"
has a real answer in here, and it is the hindcast section rather than a paraphrase of one.

**No vector store.** At ~490 KB, embeddings are infrastructure that buys nothing over BM25
and make citation harder rather than easier: a heading is a place a reader can go, and a
chunk id is not.
"""
