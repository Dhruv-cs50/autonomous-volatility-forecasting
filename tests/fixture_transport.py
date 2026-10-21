"""A transport that serves committed fixtures, so no test ever reaches the network."""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_transport(name: str) -> object:
    """Return a ``Transport`` that answers every URL with the named fixture's bytes."""
    body = (FIXTURES / name).read_bytes()

    def transport(url: str) -> bytes:
        return body

    return transport


def exploding_transport(url: str) -> bytes:
    raise AssertionError(f"a test reached the network: {url}")
