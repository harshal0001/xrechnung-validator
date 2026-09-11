"""The localised error catalogue.

`render` swallows a template/params mismatch and returns the English fallback,
which is the right behaviour for a handler — an untranslated sentence beats a
KeyError reaching the caller. It also means a mismatch is invisible: a raise site
that forgets its parameters serves English to a German caller and nothing fails.
These tests are what makes that visible instead.
"""

from __future__ import annotations

import string

import pytest

from xrv.core.messages import MESSAGES, render
from xrv.explain import LANGUAGES


def placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


@pytest.mark.parametrize("code", sorted(MESSAGES))
class TestEveryCode:
    def test_is_translated_into_every_language(self, code: str) -> None:
        assert set(MESSAGES[code]) == set(LANGUAGES)

    def test_takes_the_same_parameters_in_every_language(self, code: str) -> None:
        """A placeholder present in one language and not another renders one of
        them from the fallback, silently, for as long as nobody looks."""
        by_language = {lang: placeholders(text) for lang, text in MESSAGES[code].items()}
        assert len(set(map(frozenset, by_language.values()))) == 1, by_language

    def test_renders_when_given_its_parameters(self, code: str) -> None:
        params = dict.fromkeys(placeholders(MESSAGES[code][LANGUAGES[0]]), "x")
        for language in LANGUAGES:
            rendered = render(code, params, language, fallback="FALLBACK")
            assert rendered != "FALLBACK"
            assert "{" not in rendered


class TestFallback:
    def test_an_unknown_code_falls_back(self) -> None:
        assert render("no_such_code", {}, "de", fallback="in English") == "in English"

    def test_missing_parameters_fall_back_rather_than_raise(self) -> None:
        assert render("payload_too_large", {}, "de", fallback="in English") == "in English"
