"""
Tests for the catalogue of the two languages (spec 004, RF-08, RNF-02, ADR-1).

The catalogue is plain data, so these tests read :data:`~tlsvigil.i18n.MESSAGES` and call
:func:`~tlsvigil.i18n.translate` directly: no file, no server and no command line.
"""

from __future__ import annotations

from enum import StrEnum
from string import Formatter

import pytest

from tlsvigil.certificate import HostProblem
from tlsvigil.i18n import MESSAGES, Lang, plural, translate
from tlsvigil.rdap import DomainProblem


def _fields(template: str) -> set[str]:
    """
    Name the fields of a ``str.format`` template.

    Args:
        template (str): A sentence of the catalogue.

    Returns:
        set[str]: The field names, such as ``{"count"}``; empty for a sentence without fields.
    """
    return {field for _, field, _, _ in Formatter().parse(template) if field is not None}


# ---------------------------------------------------------------------------
# Both languages, complete and alike
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(MESSAGES))
def test_every_key_has_both_languages(key: str) -> None:
    assert set(MESSAGES[key]) == set(Lang)
    assert all(MESSAGES[key][lang].strip() for lang in Lang)


@pytest.mark.parametrize("key", sorted(MESSAGES))
def test_the_two_templates_of_a_key_name_the_same_fields(key: str) -> None:
    assert _fields(MESSAGES[key][Lang.EN]) == _fields(MESSAGES[key][Lang.PT_BR])


def test_the_field_check_sees_a_field() -> None:
    """Control for the test above: a field that differs is told apart, so equal sets mean it."""
    assert _fields("{count} days") == {"count"}
    assert _fields("{count} dias") != _fields("{total} dias")


@pytest.mark.parametrize(
    ("prefix", "problems"),
    [("host", HostProblem), ("domain", DomainProblem)],
    ids=["host", "domain"],
)
def test_every_rule_of_a_check_has_a_sentence(prefix: str, problems: type[StrEnum]) -> None:
    """A new rule in spec 002 or 003 without a sentence fails here (ADR-2)."""
    missing = [member.value for member in problems if f"{prefix}.{member.value}" not in MESSAGES]

    assert list(problems)
    assert missing == []


# ---------------------------------------------------------------------------
# The lookup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["{0}", "{x}", "{count}", "{{x}}", "{"])
def test_a_parameter_holding_braces_is_printed_as_it_is(value: str) -> None:
    """External text is data: a template inside a parameter is never filled (RNF-02)."""
    assert translate(Lang.EN, "days.other", count=value) == f"{value} days"


def test_a_field_is_filled_in_each_language() -> None:
    # The control of the test above: a plain value does reach the field.
    assert translate(Lang.EN, "days.other", count=9) == "9 days"
    assert translate(Lang.PT_BR, "days.other", count=9) == "9 dias"


def test_an_unknown_key_raises_key_error() -> None:
    with pytest.raises(KeyError):
        translate(Lang.EN, "no.such.key")


@pytest.mark.parametrize(
    ("count", "english", "portuguese"),
    [(1, "1 day", "1 dia"), (0, "0 days", "0 dias"), (2, "2 days", "2 dias")],
    ids=["one", "zero", "two"],
)
def test_only_one_is_singular(count: int, english: str, portuguese: str) -> None:
    assert plural(Lang.EN, "days", count) == english
    assert plural(Lang.PT_BR, "days", count) == portuguese


def test_the_language_values_are_the_flag_values() -> None:
    """The values are what `--lang` and the file's `lang` accept (RF-08)."""
    assert [lang.value for lang in Lang] == ["en", "pt-BR"]
