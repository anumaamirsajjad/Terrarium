"""Download published policy documents, verifying they arrived whole.

    uv run python scripts/ingest_policy.py
    uv run python scripts/ingest_policy.py --url <pdf> --slug my-doc

**Reuses the WorldPop discipline, because the failure mode is identical**: verify
`Content-Length`, write through a `.partial`, rename only once complete. A short read on a
PDF is a valid-looking file with pages missing, and pages missing from a policy document is
measures missing from an extraction — silently, and in a way the extraction cannot detect.

URL, sha256 and fetched-at go into DuckDB beside the cube's build records, so a set of
extracted measures can always be traced to the exact bytes it came from. That is the same
argument `PolicyMeasure.document_sha256` makes at the row level.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from terrarium.config import Settings, get_settings

logger = logging.getLogger("ingest_policy")

# Verified reachable in August 2026. The LDA Master Plan Lahore Division 2050 was checked
# and is **403 Forbidden** to anything without a browser session, so it is named here and
# not fetched rather than left as a URL that fails at 2 a.m.
#
#   https://lda.gop.pk/resource-center/lahore-master-plan  — 403, not usable
SOURCES: dict[str, str] = {
    "punjab-clean-air-action-plan": (
        "https://epd.punjab.gov.pk/system/files/"
        "Annex%20D2%20Punjab%20Clean%20Air%20Action%20Plan_0.pdf"
    ),
    "punjab-clean-air-policy-gazette": (
        "https://epd.punjab.gov.pk/system/files/"
        "230419%20Gazette%20Notification%20Punjab%20Clean%20Air%20Action%20Policy%20(1).pdf"
    ),
    "worldbank-punjab-clean-air-program": (
        "https://documents1.worldbank.org/curated/en/099030425204513573/pdf/"
        "P508222-5dce0c7a-e69b-435b-9570-bc1c2332de6e.pdf"
    ),
}

CATALOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS policy_documents (
    slug        TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    path        TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    bytes       BIGINT NOT NULL,
    fetched_at  TIMESTAMP NOT NULL
);
"""

# These servers are ordinary web hosts, not APIs, and at least one of them rejects
# urllib's default agent — the same Cloudflare signature ban `GroqAdapter` documents.
USER_AGENT = "terrarium/0.1 (research; contact via repository)"


def download(url: str, path: Path, *, timeout_s: float) -> Path:
    """Fetch `url` to `path` unless it is already there, verifying the length.

    Identical in shape to `ingest.pipeline._download_once`, deliberately. A `.partial`
    rename is what stops an interrupted download from being mistaken for a cache hit on
    the next run, and a `Content-Length` check is what stops a truncated PDF from being
    mistaken for a short document.
    """
    if path.exists() and path.stat().st_size > 0:
        logger.info("using cached %s", path)
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    logger.info("downloading %s -> %s", url, path)

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_s) as response, partial.open("wb") as fh:
        declared = response.headers.get("Content-Length")
        while chunk := response.read(1 << 20):
            fh.write(chunk)

    written = partial.stat().st_size
    if declared is None:
        logger.warning("%s served no Content-Length; cannot verify the download completed", url)
    elif written != int(declared):
        partial.unlink(missing_ok=True)
        raise OSError(f"truncated download: got {written} of {declared} bytes from {url}")

    if not partial.read_bytes().startswith(b"%PDF"):
        # A 200 that is an HTML error page. This host has served one, and a PDF reader's
        # complaint about it is far less legible than saying so here.
        partial.unlink(missing_ok=True)
        raise OSError(f"{url} did not return a PDF (no %PDF header); it may be gated")

    partial.replace(path)
    return path


def record(conn: duckdb.DuckDBPyConnection, *, slug: str, url: str, path: Path) -> str:
    """Write the provenance row and return the digest."""
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()

    conn.execute(CATALOG_SCHEMA)
    conn.execute("DELETE FROM policy_documents WHERE slug = ?", [slug])
    conn.execute(
        "INSERT INTO policy_documents VALUES (?, ?, ?, ?, ?, ?)",
        [slug, url, str(path), digest, len(payload), datetime.now(UTC)],
    )
    return digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Fetch one document instead of the built-in list")
    parser.add_argument("--slug", help="Filename stem for --url")
    parser.add_argument("--out", type=Path, help="Override the policy directory")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings: Settings = get_settings()
    directory = args.out or settings.policy_dir

    if args.url and not args.slug:
        parser.error("--url needs --slug")
    sources = {args.slug: args.url} if args.url else SOURCES

    conn = duckdb.connect(str(settings.duckdb_path))
    failures = 0
    try:
        for slug, url in sources.items():
            path = directory / f"{slug}.pdf"
            try:
                download(url, path, timeout_s=settings.http_timeout_s)
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                # One unreachable document is not a failed run. These are third-party
                # government hosts and this is the phase the plan flags as the one with an
                # external dependency that can rot.
                logger.error("could not fetch %s: %s", slug, exc)
                failures += 1
                continue

            digest = record(conn, slug=slug, url=url, path=path)
            logger.info(
                "%s  %s  %.0f KB  sha256 %s", slug, path, path.stat().st_size / 1024, digest[:12]
            )
    finally:
        conn.close()

    if failures:
        logger.error("%d of %d documents could not be fetched", failures, len(sources))
    return 1 if failures == len(sources) else 0


if __name__ == "__main__":
    sys.exit(main())
