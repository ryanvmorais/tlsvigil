"""
Tests for the certificate check (spec 002).

This stage covers the client context, which needs no socket; the check against the local server
joins it in the next stage.
"""

from __future__ import annotations

import ssl

from tlsvigil.certificate import create_context

# ---------------------------------------------------------------------------
# The context (RF-05, RNF-02, ADR-2)
# ---------------------------------------------------------------------------


def test_the_context_requires_tls_1_2_or_newer() -> None:
    assert create_context().minimum_version is ssl.TLSVersion.TLSv1_2


def test_the_context_verifies_the_chain_as_python_3_12_does() -> None:
    # 3.13 added both flags to the defaults; without this, the verdict would depend on the
    # Python that runs the check (ADR-2).
    flags = create_context().verify_flags

    assert not flags & ssl.VERIFY_X509_STRICT
    assert not flags & ssl.VERIFY_X509_PARTIAL_CHAIN


def test_the_context_keeps_the_default_verification_flags() -> None:
    # Positive control for the test above: clearing two flags must not clear the rest.
    flags = create_context().verify_flags

    assert flags & ssl.VERIFY_X509_TRUSTED_FIRST


def test_the_context_matches_the_host_against_the_subject_alternative_names_only() -> None:
    assert create_context().hostname_checks_common_name is False


def test_the_context_requires_a_verified_certificate_for_the_host() -> None:
    context = create_context()

    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True
