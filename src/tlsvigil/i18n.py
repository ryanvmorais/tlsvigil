"""
The two languages of the command: the catalogue of sentences and the lookup (spec 004, ADR-1).

Every sentence the command writes lives in :data:`MESSAGES`, once per language, as a
:meth:`str.format` template with named fields. :func:`translate` fills a template with
:meth:`str.format_map`, so a parameter is inserted as it is and never read as a template itself
(RNF-02). Text from the outside (a certificate, an RDAP answer, the file) is a parameter, never a
key, and is not translated (RF-08).

A rule of a check (a :class:`~tlsvigil.certificate.HostProblem` or a
:class:`~tlsvigil.rdap.DomainProblem`) has one sentence under ``host.<code>`` or
``domain.<code>``; the tests check that every member has one, in both languages.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Final

__all__ = ["MESSAGES", "Lang", "plural", "translate"]


# ---------------------------------------------------------------------------
# Languages
# ---------------------------------------------------------------------------


class Lang(StrEnum):
    """
    A language of the output (RF-08).

    Attributes:
        EN (str): English, the default.
        PT_BR (str): Brazilian Portuguese.
    """

    EN = "en"
    PT_BR = "pt-BR"


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

# One entry per sentence, both languages side by side, so a sentence added in one language
# alone is seen in review and fails the parity test (ADR-1).
MESSAGES: Final[Mapping[str, Mapping[Lang, str]]] = {
    # Counts: `plural` picks the key; both languages call 1 singular and every other count plural.
    "days.one": {
        Lang.EN: "{count} day",
        Lang.PT_BR: "{count} dia",
    },
    "days.other": {
        Lang.EN: "{count} days",
        Lang.PT_BR: "{count} dias",
    },
    # The rules of a host and a port (spec 002, RF-06; HostProblem).
    "host.empty": {
        Lang.EN: "the host is empty",
        Lang.PT_BR: "o host está vazio",
    },
    "host.whitespace": {
        Lang.EN: "the host must not contain whitespace",
        Lang.PT_BR: "o host não pode ter espaços",
    },
    "host.url_or_path": {
        Lang.EN: "the host must be a name or an IP address, not a URL or a path",
        Lang.PT_BR: "o host deve ser um nome ou um endereço IP, não uma URL nem um caminho",
    },
    "host.port_in_host": {
        Lang.EN: "the host must not contain a port or a ':' unless it is an IPv6 address",
        Lang.PT_BR: "o host não pode ter porta nem ':', a não ser que seja um endereço IPv6",
    },
    "host.port": {
        Lang.EN: "the port must be an integer from 1 to 65535",
        Lang.PT_BR: "a porta deve ser um número inteiro de 1 a 65535",
    },
    # The rules of a domain (spec 003, RF-07; DomainProblem).
    "domain.empty": {
        Lang.EN: "the domain is empty",
        Lang.PT_BR: "o domínio está vazio",
    },
    "domain.whitespace": {
        Lang.EN: "the domain must not contain whitespace",
        Lang.PT_BR: "o domínio não pode ter espaços",
    },
    "domain.url_or_path": {
        Lang.EN: "the domain must be a name, not a URL or a path",
        Lang.PT_BR: "o domínio deve ser um nome, não uma URL nem um caminho",
    },
    "domain.port": {
        Lang.EN: "the domain must not contain a port or a ':'",
        Lang.PT_BR: "o domínio não pode ter porta nem ':'",
    },
    "domain.user_info": {
        Lang.EN: "the domain must not contain user information ('@')",
        Lang.PT_BR: "o domínio não pode ter informação de usuário ('@')",
    },
    "domain.underscore": {
        Lang.EN: "the domain must contain letters, digits, hyphens and dots only",
        Lang.PT_BR: "o domínio só pode ter letras, dígitos, hífens e pontos",
    },
    "domain.not_ascii": {
        Lang.EN: "write the domain in its ASCII form, the 'xn--' form",
        Lang.PT_BR: "escreva o domínio na forma ASCII, a forma 'xn--'",
    },
    "domain.one_label": {
        Lang.EN: "the domain must have at least two labels, such as example.com",
        Lang.PT_BR: "o domínio precisa de pelo menos dois rótulos, como example.com",
    },
    "domain.empty_label": {
        Lang.EN: "the domain must not have an empty label",
        Lang.PT_BR: "o domínio não pode ter um rótulo vazio",
    },
    "domain.bad_label": {
        Lang.EN: "each label must be 1 to 63 letters, digits or inner hyphens",
        Lang.PT_BR: "cada rótulo deve ter de 1 a 63 letras, dígitos ou hífens internos",
    },
    "domain.too_long": {
        Lang.EN: "the domain must be 253 characters or fewer",
        Lang.PT_BR: "o domínio deve ter no máximo 253 caracteres",
    },
    "domain.numeric": {
        Lang.EN: "the domain must not end in a numeric label (an IP address is not a domain)",
        Lang.PT_BR: "o domínio não pode terminar num rótulo numérico (endereço IP não é domínio)",
    },
}


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def translate(lang: Lang, key: str, **params: str | int) -> str:
    """
    Give the sentence of a key in a language, with its fields filled.

    Args:
        lang (Lang): The language of the output.
        key (str): A key of :data:`MESSAGES`.
        **params (str | int): The value of each named field of the template.

    Returns:
        str: The sentence. A parameter is inserted as it is: braces inside it stay braces.

    Raises:
        KeyError: When the key is not in the catalogue, or a field of the template has no
            parameter.
    """
    # format_map reads only the template, which is ours; never format the result again.
    return MESSAGES[key][lang].format_map(params)


def plural(lang: Lang, key: str, count: int, **params: str | int) -> str:
    """
    Give the singular or the plural sentence of a count.

    The catalogue holds ``<key>.one`` and ``<key>.other``. English and Portuguese agree that only
    ``1`` is singular, so no plural rule table is needed (spec 004, ADR-1).

    Args:
        lang (Lang): The language of the output.
        key (str): The stem of the two keys, such as ``days``.
        count (int): The number shown, filled into the ``count`` field; a count of days already
            passed is given as a positive number.
        **params (str | int): The other fields of the template, if any.

    Returns:
        str: The sentence, such as ``1 day`` or ``9 dias``.

    Raises:
        KeyError: When the stem has no such key in the catalogue.
    """
    form = "one" if count == 1 else "other"
    return translate(lang, f"{key}.{form}", count=count, **params)
