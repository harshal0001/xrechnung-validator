#!/usr/bin/env python
"""
measure_latency.py — how long a deployed service takes to validate a document.

Sends every reference message to `/validate` several times and reports the
distribution of `duration_ms`, the service's own measurement of the work:
identifying the document, the schema, the stylesheets, the scenario. That is
the figure in the README. The round trip is printed beside it and is not — it
measures the network between here and Frankfurt.

    python scripts/measure_latency.py https://xrechnung.harshalkothari.tech \
        --corpus tests/corpus/_downloaded/2026-08-31/instances/standard

The first pass is discarded: it is the one that finds a cold environment.
Standard library only, so it runs anywhere the corpus has been fetched.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request
import uuid
from pathlib import Path

#: Seconds between the starts of two requests: four a second.
PACE = 0.25


def validate(url: str, document: bytes) -> float:
    """Upload one document and return the service's `duration_ms` for it."""
    boundary = uuid.uuid4().hex
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="invoice.xml"\r\n'
            "Content-Type: application/xml\r\n\r\n"
        ).encode()
        + document
        + f"\r\n--{boundary}--\r\n".encode()
    )
    request = urllib.request.Request(
        f"{url.rstrip('/')}/validate",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return float(json.load(response)["duration_ms"])


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("url")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument(
        "--passes", type=int, default=3, help="measured passes, after one unmeasured"
    )
    args = parser.parse_args()

    documents = [path.read_bytes() for path in sorted(args.corpus.glob("*.xml"))]
    if not documents:
        print(f"no reference messages under {args.corpus}", file=sys.stderr)
        return 1

    served: list[float] = []
    round_trip: list[float] = []
    for measured in (False, *[True] * args.passes):
        for document in documents:
            started = time.perf_counter()
            duration = validate(args.url, document)
            elapsed = time.perf_counter() - started
            if measured:
                served.append(duration)
                round_trip.append(elapsed * 1000)
            # The public endpoint is throttled at five requests a second. Staying
            # under it measures the service; going over measures the throttle.
            time.sleep(max(0.0, PACE - elapsed))

    print(f"{len(served)} requests over {len(documents)} reference messages")
    print(
        f"validation, measured by the service: "
        f"p50 {percentile(served, 0.5):.0f} ms, p95 {percentile(served, 0.95):.0f} ms, "
        f"p99 {percentile(served, 0.99):.0f} ms, mean {statistics.mean(served):.0f} ms"
    )
    print(
        f"round trip from here: "
        f"p50 {percentile(round_trip, 0.5):.0f} ms, p95 {percentile(round_trip, 0.95):.0f} ms"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
