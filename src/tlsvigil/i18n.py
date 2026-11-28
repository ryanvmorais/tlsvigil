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
    # The status column of the text output (RF-06): each label differs, so an attention line is
    # told apart without colour.
    "status.ok": {
        Lang.EN: "OK",
        Lang.PT_BR: "OK",
    },
    "status.expiring": {
        Lang.EN: "EXPIRING",
        Lang.PT_BR: "VENCENDO",
    },
    "status.invalid": {
        Lang.EN: "INVALID",
        Lang.PT_BR: "INVÁLIDO",
    },
    "status.unknown": {
        Lang.EN: "UNKNOWN",
        Lang.PT_BR: "DESCONHECIDO",
    },
    "status.error": {
        Lang.EN: "ERROR",
        Lang.PT_BR: "ERRO",
    },
    # The detail of a certificate line (RF-06); {days} is a `days` count, {date} the UTC date.
    "line.certificate.expires": {
        Lang.EN: "certificate expires in {days} ({date})",
        Lang.PT_BR: "certificado vence em {days} ({date})",
    },
    "line.certificate.today": {
        Lang.EN: "certificate expires today ({date})",
        Lang.PT_BR: "certificado vence hoje ({date})",
    },
    "line.certificate.invalid": {
        Lang.EN: "certificate failed verification: {reason}",
        Lang.PT_BR: "certificado não passou na verificação: {reason}",
    },
    "line.certificate.error": {
        Lang.EN: "certificate not checked: {kind}",
        Lang.PT_BR: "certificado não verificado: {kind}",
    },
    # The detail of a domain line (RF-06); a passed date is given as a positive count of days.
    "line.domain.expires": {
        Lang.EN: "domain expires in {days} ({date})",
        Lang.PT_BR: "domínio vence em {days} ({date})",
    },
    "line.domain.today": {
        Lang.EN: "domain expires today ({date})",
        Lang.PT_BR: "domínio vence hoje ({date})",
    },
    "line.domain.expired": {
        Lang.EN: "domain expired {days} ago ({date})",
        Lang.PT_BR: "domínio venceu há {days} ({date})",
    },
    "line.domain.unknown": {
        Lang.EN: "domain expiry unknown: {reason}",
        Lang.PT_BR: "vencimento do domínio desconhecido: {reason}",
    },
    "line.domain.error": {
        Lang.EN: "domain not checked: {kind}",
        Lang.PT_BR: "domínio não verificado: {kind}",
    },
    # The last line of the text output (RF-06): "6 checked: 3 need attention."
    "summary": {
        Lang.EN: "{checked}: {attention}.",
        Lang.PT_BR: "{checked}: {attention}.",
    },
    "checked.one": {
        Lang.EN: "{count} checked",
        Lang.PT_BR: "{count} verificado",
    },
    "checked.other": {
        Lang.EN: "{count} checked",
        Lang.PT_BR: "{count} verificados",
    },
    "attention.one": {
        Lang.EN: "{count} needs attention",
        Lang.PT_BR: "{count} precisa de atenção",
    },
    "attention.other": {
        Lang.EN: "{count} need attention",
        Lang.PT_BR: "{count} precisam de atenção",
    },
    # Why a verification failed (spec 002, RF-03; InvalidReason).
    "reason.certificate.expired": {
        Lang.EN: "expired",
        Lang.PT_BR: "vencido",
    },
    "reason.certificate.not_yet_valid": {
        Lang.EN: "not valid yet",
        Lang.PT_BR: "ainda não é válido",
    },
    "reason.certificate.hostname_mismatch": {
        Lang.EN: "the certificate is not for this host",
        Lang.PT_BR: "o certificado não é deste host",
    },
    "reason.certificate.untrusted": {
        Lang.EN: "the chain does not lead to a trusted authority",
        Lang.PT_BR: "a cadeia não leva a uma autoridade confiável",
    },
    "reason.certificate.other": {
        Lang.EN: "another verification failure",
        Lang.PT_BR: "outra falha na verificação",
    },
    # Why a host could not be checked (spec 002, RF-04; certificate.ErrorKind).
    "error.certificate.dns": {
        Lang.EN: "the name does not resolve",
        Lang.PT_BR: "o nome não resolve",
    },
    "error.certificate.refused": {
        Lang.EN: "the connection was refused",
        Lang.PT_BR: "a conexão foi recusada",
    },
    "error.certificate.timeout": {
        Lang.EN: "no answer within the timeout",
        Lang.PT_BR: "sem resposta dentro do timeout",
    },
    "error.certificate.tls": {
        Lang.EN: "the TLS handshake failed",
        Lang.PT_BR: "o handshake TLS falhou",
    },
    "error.certificate.other": {
        Lang.EN: "an unexpected failure",
        Lang.PT_BR: "uma falha inesperada",
    },
    # Why the date of a domain is unknown (spec 003, RF-03; UnknownReason).
    "reason.domain.no_rdap_service": {
        Lang.EN: "the registry has no RDAP service",
        Lang.PT_BR: "o registro não tem serviço RDAP",
    },
    "reason.domain.no_expiration": {
        Lang.EN: "the registry gives no expiry date",
        Lang.PT_BR: "o registro não informa a data de vencimento",
    },
    # Why a domain could not be checked (spec 003, RF-04 to RF-06; rdap.ErrorKind).
    "error.domain.bootstrap": {
        Lang.EN: "the IANA list of RDAP servers could not be read",
        Lang.PT_BR: "a lista de servidores RDAP da IANA não pôde ser lida",
    },
    "error.domain.dns": {
        Lang.EN: "the name of the RDAP server does not resolve",
        Lang.PT_BR: "o nome do servidor RDAP não resolve",
    },
    "error.domain.refused": {
        Lang.EN: "the RDAP server refused the connection",
        Lang.PT_BR: "o servidor RDAP recusou a conexão",
    },
    "error.domain.timeout": {
        Lang.EN: "the RDAP server did not answer within the timeout",
        Lang.PT_BR: "o servidor RDAP não respondeu dentro do timeout",
    },
    "error.domain.tls": {
        Lang.EN: "the TLS connection to the RDAP server failed",
        Lang.PT_BR: "a conexão TLS com o servidor RDAP falhou",
    },
    "error.domain.not_found": {
        Lang.EN: "the registry has no such domain",
        Lang.PT_BR: "o registro não tem esse domínio",
    },
    "error.domain.rate_limited": {
        Lang.EN: "the registry is limiting requests; try again later",
        Lang.PT_BR: "o registro está limitando as consultas; tente de novo mais tarde",
    },
    "error.domain.http": {
        Lang.EN: "the RDAP server answered with an error",
        Lang.PT_BR: "o servidor RDAP respondeu com erro",
    },
    "error.domain.redirect": {
        Lang.EN: "the RDAP server redirected to another place",
        Lang.PT_BR: "o servidor RDAP redirecionou para outro lugar",
    },
    "error.domain.too_large": {
        Lang.EN: "the RDAP answer is too large",
        Lang.PT_BR: "a resposta RDAP é grande demais",
    },
    "error.domain.invalid_response": {
        Lang.EN: "the RDAP answer is not a valid domain record",
        Lang.PT_BR: "a resposta RDAP não é um registro de domínio válido",
    },
    "error.domain.other": {
        Lang.EN: "an unexpected failure",
        Lang.PT_BR: "uma falha inesperada",
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
    # The [notifications] table (spec 005, RF-02); a secret-like key names its variables only.
    "config.channel": {
        Lang.EN: 'the channel must be "github", "telegram" or "smtp"',
        Lang.PT_BR: 'o canal deve ser "github", "telegram" ou "smtp"',
    },
    "config.duplicate_channel": {
        Lang.EN: "this channel is already listed at {first}",
        Lang.PT_BR: "este canal já está listado em {first}",
    },
    "config.smtp_table": {
        Lang.EN: 'the "smtp" channel needs a [notifications.smtp] table with the server\'s host',
        Lang.PT_BR: (
            'o canal "smtp" precisa de uma tabela [notifications.smtp] com o host do servidor'
        ),
    },
    "config.smtp_security": {
        Lang.EN: 'security must be "starttls" or "tls"',
        Lang.PT_BR: 'security deve ser "starttls" ou "tls"',
    },
    "config.secret_key": {
        Lang.EN: (
            "a secret or an address does not belong in the file; "
            "put it in the environment instead: {variables}"
        ),
        Lang.PT_BR: ("segredo ou endereço não vai no arquivo; coloque-o no ambiente: {variables}"),
    },
    # The environment variables of the channels (spec 005, RF-03): the place is the variable's
    # name, and no sentence repeats its value.
    "env.missing": {
        Lang.EN: "the environment variable is not set, or is empty",
        Lang.PT_BR: "a variável de ambiente não está definida, ou está vazia",
    },
    "env.invalid.token": {
        Lang.EN: "the token must not contain whitespace or control characters",
        Lang.PT_BR: "o token não pode ter espaços nem caracteres de controle",
    },
    "env.invalid.repository": {
        Lang.EN: "must be the repository written as owner/name",
        Lang.PT_BR: "deve ser o repositório escrito como dono/nome",
    },
    "env.invalid.telegram_token": {
        Lang.EN: "must be a Telegram bot token: digits, ':', then letters, digits, '_' or '-'",
        Lang.PT_BR: (
            "deve ser um token de bot do Telegram: dígitos, ':', depois letras, dígitos, '_' ou '-'"
        ),
    },
    "env.invalid.chat_id": {
        Lang.EN: (
            "must be the number of a chat, possibly negative, "
            "or '@' and a name of 5 to 32 letters, digits or '_'"
        ),
        Lang.PT_BR: (
            "deve ser o número de um chat, talvez negativo, "
            "ou '@' e um nome de 5 a 32 letras, dígitos ou '_'"
        ),
    },
    "env.invalid.text": {
        Lang.EN: "must not contain control characters",
        Lang.PT_BR: "não pode ter caracteres de controle",
    },
    "env.invalid.address": {
        Lang.EN: "must be one e-mail address written as name@domain, without a display name",
        Lang.PT_BR: (
            "deve ser um endereço de e-mail escrito como nome@domínio, sem nome de exibição"
        ),
    },
    "env.invalid.addresses": {
        Lang.EN: (
            "must be one or more e-mail addresses written as name@domain, separated by commas"
        ),
        Lang.PT_BR: (
            "deve ser um ou mais endereços de e-mail escritos como nome@domínio, "
            "separados por vírgulas"
        ),
    },
    # The state file (spec 005, RF-07; ADR-2): why it was read as empty, so the run counts as
    # the first; and why it could not be written, so the next run repeats a message.
    "state.unreadable": {
        Lang.EN: "The state file {path} cannot be read; this run counts as the first.",
        Lang.PT_BR: (
            "O arquivo de estado {path} não pode ser lido; " "esta execução conta como a primeira."
        ),
    },
    "state.too_large": {
        Lang.EN: (
            "The state file {path} is larger than {limit} KiB; this run counts as the first."
        ),
        Lang.PT_BR: (
            "O arquivo de estado {path} passa de {limit} KiB; esta execução conta como a primeira."
        ),
    },
    "state.not_utf8": {
        Lang.EN: "The state file {path} is not UTF-8; this run counts as the first.",
        Lang.PT_BR: (
            "O arquivo de estado {path} não está em UTF-8; " "esta execução conta como a primeira."
        ),
    },
    "state.invalid": {
        Lang.EN: "The state file {path} is not valid; this run counts as the first.",
        Lang.PT_BR: "O arquivo de estado {path} não é válido; esta execução conta como a primeira.",
    },
    "state.version": {
        Lang.EN: (
            "The state file {path} was written by another version of TLSVigil; "
            "this run counts as the first."
        ),
        Lang.PT_BR: (
            "O arquivo de estado {path} foi gravado por outra versão do TLSVigil; "
            "esta execução conta como a primeira."
        ),
    },
    "state.unwritable": {
        Lang.EN: (
            "The state file {path} cannot be written; the next run may repeat a notification."
        ),
        Lang.PT_BR: (
            "O arquivo de estado {path} não pode ser gravado; "
            "a próxima execução pode repetir uma notificação."
        ),
    },
    # The command (RF-02, RF-03; ADR-3): the headings of a configuration error, the hint of a
    # missing default file, and the line before the traceback of a bug.
    "cli.file_heading": {
        Lang.EN: "Nothing was checked: the configuration {path} cannot be used.",
        Lang.PT_BR: "Nada foi verificado: a configuração {path} não pode ser usada.",
    },
    "cli.names_heading": {
        Lang.EN: "Nothing was checked: the names given with --host and --domain cannot be used.",
        Lang.PT_BR: (
            "Nada foi verificado: os nomes passados com --host e --domain não podem ser usados."
        ),
    },
    "cli.missing_hint": {
        Lang.EN: (
            "Name what to check with --host HOST[:PORT] or --domain NAME, "
            "or write a {path} file in this directory."
        ),
        Lang.PT_BR: (
            "Diga o que verificar com --host HOST[:PORTA] ou --domain NOME, "
            "ou crie um arquivo {path} nesta pasta."
        ),
    },
    "cli.internal_error": {
        Lang.EN: "tlsvigil: internal error; this is a bug, please report it with the text below.",
        Lang.PT_BR: "tlsvigil: erro interno; isto é um defeito, informe-o com o texto abaixo.",
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
