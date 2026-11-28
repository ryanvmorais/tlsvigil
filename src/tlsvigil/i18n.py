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
    # The configuration file itself (RF-02; the place of these problems is empty).
    "config.missing": {
        Lang.EN: "the file {path} does not exist",
        Lang.PT_BR: "o arquivo {path} não existe",
    },
    "config.directory": {
        Lang.EN: "{path} is a directory, not a file",
        Lang.PT_BR: "{path} é uma pasta, não um arquivo",
    },
    "config.permission": {
        Lang.EN: "no permission to read {path}",
        Lang.PT_BR: "sem permissão para ler {path}",
    },
    "config.unreadable": {
        Lang.EN: "could not read {path}: {detail}",
        Lang.PT_BR: "não foi possível ler {path}: {detail}",
    },
    "config.too_large": {
        Lang.EN: "{path} is larger than {limit} KiB, too large for a configuration file",
        Lang.PT_BR: "{path} passa de {limit} KiB, grande demais para um arquivo de configuração",
    },
    "config.not_utf8": {
        Lang.EN: "{path} is not UTF-8 text",
        Lang.PT_BR: "{path} não é texto em UTF-8",
    },
    "config.toml_syntax": {
        Lang.EN: "invalid TOML at line {line}, column {column}: {detail}",
        Lang.PT_BR: "TOML inválido na linha {line}, coluna {column}: {detail}",
    },
    # The shape of the configuration (RF-04, RF-05; the place names the key or the entry).
    "config.unknown_key": {
        Lang.EN: "unknown key; check its spelling",
        Lang.PT_BR: "chave desconhecida; confira a grafia",
    },
    "config.not_a_table": {
        Lang.EN: "must be a table",
        Lang.PT_BR: "deve ser uma tabela",
    },
    "config.not_a_list": {
        Lang.EN: "must be a list",
        Lang.PT_BR: "deve ser uma lista",
    },
    "config.not_a_string": {
        Lang.EN: "must be a string",
        Lang.PT_BR: "deve ser um texto entre aspas",
    },
    "config.missing_key": {
        Lang.EN: "the entry needs the key {key}",
        Lang.PT_BR: "a entrada precisa da chave {key}",
    },
    "config.bad_host_entry": {
        Lang.EN: 'must be a string such as "example.com:443" or an inline table with host',
        Lang.PT_BR: 'deve ser um texto como "example.com:443" ou uma tabela em linha com host',
    },
    "config.bad_domain_entry": {
        Lang.EN: 'must be a string such as "example.com" or an inline table with name',
        Lang.PT_BR: 'deve ser um texto como "example.com" ou uma tabela em linha com name',
    },
    "config.lang": {
        Lang.EN: 'the language must be "en" or "pt-BR"',
        Lang.PT_BR: 'o idioma deve ser "en" ou "pt-BR"',
    },
    "config.timeout": {
        Lang.EN: "the timeout must be a number of seconds greater than 0",
        Lang.PT_BR: "o timeout deve ser um número de segundos maior que 0",
    },
    "config.warn_days": {
        Lang.EN: "warn_days must be a whole number, 0 or more",
        Lang.PT_BR: "warn_days deve ser um número inteiro, 0 ou mais",
    },
    "config.host_syntax": {
        Lang.EN: "write the host as host, host:port or [IPv6]:port",
        Lang.PT_BR: "escreva o host como host, host:porta ou [IPv6]:porta",
    },
    "config.brackets": {
        Lang.EN: "only an IPv6 address goes in brackets",
        Lang.PT_BR: "só um endereço IPv6 vai entre colchetes",
    },
    "config.duplicate_host": {
        Lang.EN: "this host and port are already listed at {first}",
        Lang.PT_BR: "este host e esta porta já estão listados em {first}",
    },
    "config.duplicate_domain": {
        Lang.EN: "this domain is already listed at {first}",
        Lang.PT_BR: "este domínio já está listado em {first}",
    },
    "config.nothing_to_check": {
        Lang.EN: "nothing to check: no host and no domain is listed",
        Lang.PT_BR: "nada a verificar: nenhum host e nenhum domínio listado",
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
