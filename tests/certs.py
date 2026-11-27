"""
Test certificates that ``trustme`` has no option for, made with ``cryptography``.

``trustme`` covers the common cases (a CA, an intermediate, leaves with chosen names and dates).
Two cases need a hand-built certificate: a self-signed leaf, and a leaf without an Authority Key
Identifier, which browsers and Python 3.12 accept and ``VERIFY_X509_STRICT`` refuses (spec 002,
ADR-2 and ADR-4). Each comes back as a :class:`PemLeaf` that the ``serve`` fixture loads like a
``trustme`` leaf. Keys are ECDSA P-256, quick to make.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import trustme
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

# A validity window around the moment the certificate is made, wide enough that no test runs
# outside it and short enough to look like a real leaf.
_VALID_BEFORE = dt.timedelta(hours=1)
_VALID_FOR = dt.timedelta(days=30)

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PemLeaf:
    """
    A leaf certificate as PEM, in the shape the ``serve`` fixture loads.

    Attributes:
        chain_pem (list[bytes]): The leaf first, then any intermediate, one PEM block each.
        private_key_pem (bytes): The leaf's private key, unencrypted PKCS#8 PEM.
    """

    chain_pem: list[bytes]
    private_key_pem: bytes


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _leaf_builder(
    name: str, issuer: x509.Name, public_key: ec.EllipticCurvePublicKey
) -> x509.CertificateBuilder:
    """
    Start a leaf certificate for ``name``, valid now.

    Args:
        name (str): The DNS name the certificate is for (subject common name and SAN).
        issuer (x509.Name): The issuer's name.
        public_key (ec.EllipticCurvePublicKey): The leaf's public key.

    Returns:
        x509.CertificateBuilder: The builder, ready for more extensions and the signature.
    """
    now = dt.datetime.now(dt.UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _VALID_BEFORE)
        .not_valid_after(now + _VALID_FOR)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )


def _key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    """
    Serialize ``key`` the way ``ssl.SSLContext.load_cert_chain`` reads it.

    Args:
        key (ec.EllipticCurvePrivateKey): The private key.

    Returns:
        bytes: Unencrypted PKCS#8 PEM.
    """
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def self_signed_leaf(name: str = "localhost") -> PemLeaf:
    """
    Make a leaf that signs itself: no authority stands behind it.

    Args:
        name (str): The DNS name the certificate is for.

    Returns:
        PemLeaf: The certificate and its key.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    cert = _leaf_builder(name, subject, key.public_key()).sign(key, hashes.SHA256())
    return PemLeaf([cert.public_bytes(serialization.Encoding.PEM)], _key_pem(key))


def leaf_without_aki(ca: trustme.CA, name: str = "localhost") -> PemLeaf:
    """
    Make a leaf signed by ``ca`` that carries no Authority Key Identifier.

    Args:
        ca (trustme.CA): The authority that signs it (an ECDSA ``trustme`` CA, the default).
        name (str): The DNS name the certificate is for.

    Returns:
        PemLeaf: The certificate and its key.

    Raises:
        TypeError: When ``ca`` does not use an elliptic-curve key.
    """
    ca_key = serialization.load_pem_private_key(ca.private_key_pem.bytes(), password=None)
    if not isinstance(ca_key, ec.EllipticCurvePrivateKey):
        raise TypeError("leaf_without_aki expects a trustme CA with an ECDSA key")
    ca_cert = x509.load_pem_x509_certificate(ca.cert_pem.bytes())
    key = ec.generate_private_key(ec.SECP256R1())
    cert = _leaf_builder(name, ca_cert.subject, key.public_key()).sign(ca_key, hashes.SHA256())
    return PemLeaf([cert.public_bytes(serialization.Encoding.PEM)], _key_pem(key))
