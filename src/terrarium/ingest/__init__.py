"""Layer 1a - Ingest: the ONLY package permitted to perform network I/O.

STAC search, COG reads, and vector pulls live here. Everything downstream consumes
the aligned cube produced by `terrarium.state`, never a remote source directly.
"""
