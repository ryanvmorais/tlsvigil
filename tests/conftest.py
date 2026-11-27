"""
Shared fixtures of the test suite: the network guard, the test CA and the local TLS server.

No test reaches a real network (``specs/README.md``, "How a spec is tested"): the autouse
``network_guard`` installs :mod:`network_guard` for every test. The certificates are made during
the test with ``trustme`` (spec 002, ADR-4), and ``serve`` starts a
:class:`~tls_server.LocalServer` on ``127.0.0.1`` for one of them.
"""

from __future__ import annotations

import itertools
import ssl
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest
import trustme

from certs import PemLeaf
from network_guard import NetworkGuard, install
from tls_server import LocalServer

# What `serve` accepts: a trustme leaf, or one of the certificates of `certs.py`.
Leaf = trustme.LeafCert | PemLeaf

# ---------------------------------------------------------------------------
# Network guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def network_guard(monkeypatch: pytest.MonkeyPatch) -> NetworkGuard:
    """
    Refuse, for the whole test, any lookup or connection outside the loopback interface.

    Args:
        monkeypatch (pytest.MonkeyPatch): Undoes the wrappers when the test ends.

    Returns:
        NetworkGuard: The record of the lookups and connections the test made; a test asks for
            this fixture by name to assert on it.
    """
    return install(monkeypatch)


# ---------------------------------------------------------------------------
# Test CA and local TLS server
# ---------------------------------------------------------------------------


@pytest.fixture
def ca() -> trustme.CA:
    """
    Make a fresh certificate authority for one test.

    Returns:
        trustme.CA: A root CA no system trust store knows; a client trusts it only when the
            test adds it (``ca.configure_trust(context)``).
    """
    return trustme.CA()


def _pem_files(leaf: Leaf, *, full_chain: bool, directory: Path) -> tuple[Path, Path]:
    """
    Write the certificate chain and the private key of ``leaf`` to two PEM files.

    Args:
        leaf (Leaf): The certificate to serve.
        full_chain (bool): ``True`` to write the leaf and its intermediates, ``False`` for the
            leaf alone (a server that forgets its intermediate).
        directory (Path): Where to write the files.

    Returns:
        tuple[Path, Path]: The certificate file and the key file.
    """
    if isinstance(leaf, PemLeaf):
        chain = leaf.chain_pem if full_chain else leaf.chain_pem[:1]
        key = leaf.private_key_pem
    else:
        blobs = leaf.cert_chain_pems if full_chain else leaf.cert_chain_pems[:1]
        chain = [blob.bytes() for blob in blobs]
        key = leaf.private_key_pem.bytes()
    cert_file = directory / "chain.pem"
    key_file = directory / "key.pem"
    cert_file.write_bytes(b"".join(chain))
    key_file.write_bytes(key)
    return cert_file, key_file


@pytest.fixture
def serve(tmp_path: Path) -> Iterator[Callable[..., LocalServer]]:
    """
    Start local TLS servers for the test, and stop them when it ends.

    Args:
        tmp_path (Path): Holds the PEM files the servers load.

    Yields:
        Callable[..., LocalServer]: ``start(leaf, full_chain=True)``, which starts a server on
            ``127.0.0.1`` presenting ``leaf`` and returns it.
    """
    numbers = itertools.count()
    with ExitStack() as stack:

        def start(leaf: Leaf, *, full_chain: bool = True) -> LocalServer:
            """
            Start a server presenting ``leaf``; it stops when the test ends.

            Args:
                leaf (Leaf): The certificate to serve.
                full_chain (bool): ``False`` to send the leaf without its intermediates.

            Returns:
                LocalServer: The running server.
            """
            directory = tmp_path / f"server-{next(numbers)}"  # one folder per server
            directory.mkdir()
            cert_file, key_file = _pem_files(leaf, full_chain=full_chain, directory=directory)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert_file, key_file)
            return stack.enter_context(LocalServer("tls", context))

        yield start
