"""Does the validator catch a broken invoice, and only what is broken?

The reference corpus tests prove no false positives. These prove the other
direction — that a rule which should fire does. Both are needed: a validator that
reports nothing passes the first set perfectly.

Each case takes a valid reference message, deletes one required thing, and asserts
the corresponding rule fires. It then asserts nothing *else* blocking fires, which
is the part that makes the number meaningful: a validator that flags every
document is no more useful than one that flags none.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mutation.catalogue import MUTATIONS, Mutation, MutationError
from xrv.core import Syntax
from xrv.validate import ValidationEngine

pytestmark = pytest.mark.usefixtures("corpus")

#: One reference message per syntax is enough. These mutations target header
#: fields that every business case carries, so a wider base would add runtime
#: without adding coverage.
BASE = {Syntax.UBL: "01.01a-INVOICE_ubl.xml", Syntax.CII: "01.01a-INVOICE_uncefact.xml"}


@pytest.fixture(scope="module")
def mutate(corpus: Path, tmp_path_factory: pytest.TempPathFactory):
    """Apply a mutation to its base invoice and return the written file."""
    out = tmp_path_factory.mktemp("mutated")

    def apply(mutation: Mutation) -> Path:
        target = out / f"{mutation.name}.xml"
        if not target.exists():
            target.write_bytes(mutation.apply(corpus / BASE[mutation.syntax]))
        return target

    return apply


def ids(mutations: tuple[Mutation, ...]) -> list[str]:
    return [m.name for m in mutations]


class TestTheHarnessItself:
    """A mutation harness that quietly fails to mutate proves nothing."""

    def test_every_base_invoice_exists(self, corpus: Path) -> None:
        for name in BASE.values():
            assert (corpus / name).is_file()

    @pytest.mark.parametrize("mutation", MUTATIONS, ids=ids(MUTATIONS))
    def test_mutation_actually_changes_the_document(self, mutation: Mutation, corpus: Path) -> None:
        original = (corpus / BASE[mutation.syntax]).read_bytes()
        assert mutation.apply(corpus / BASE[mutation.syntax]) != original

    def test_a_selector_that_matches_nothing_is_an_error(self, corpus: Path) -> None:
        """Not a silent no-op: that would turn a test into an assertion about a
        valid document, which passes for entirely the wrong reason."""
        nonsense = Mutation(
            rule_id="BR-00", syntax=Syntax.UBL, breaks="nothing", xpath="/*/cbc:NoSuchElement"
        )
        with pytest.raises(MutationError, match="matched nothing"):
            nonsense.apply(corpus / BASE[Syntax.UBL])

    def test_the_base_invoices_are_clean_before_mutation(
        self, engine: ValidationEngine, corpus: Path
    ) -> None:
        """If a base document already failed, every result below would be noise."""
        for syntax, name in BASE.items():
            blocking = [f for f in engine.findings(corpus / name, syntax) if f.blocking]
            assert not blocking, f"{name} is not clean to begin with: {blocking}"


class TestRulesFire:
    @pytest.mark.parametrize("mutation", MUTATIONS, ids=ids(MUTATIONS))
    def test_the_broken_rule_is_reported(
        self, engine: ValidationEngine, mutate, mutation: Mutation
    ) -> None:
        findings = engine.findings(mutate(mutation), mutation.syntax)
        fired = {f.rule_id for f in findings if f.blocking}
        assert mutation.rule_id in fired, (
            f"{mutation.breaks} — expected {mutation.rule_id}, got {sorted(fired) or 'nothing'}"
        )

    @pytest.mark.parametrize("mutation", MUTATIONS, ids=ids(MUTATIONS))
    def test_nothing_unexpected_is_reported(
        self, engine: ValidationEngine, mutate, mutation: Mutation
    ) -> None:
        """The half that stops "catches everything" from looking like accuracy."""
        findings = engine.findings(mutate(mutation), mutation.syntax)
        fired = {f.rule_id for f in findings if f.blocking}
        assert not fired - mutation.expected, (
            f"{mutation.breaks} — unexpected rules also fired: {sorted(fired - mutation.expected)}"
        )

    @pytest.mark.parametrize("mutation", MUTATIONS, ids=ids(MUTATIONS))
    def test_the_finding_carries_text_to_explain(
        self, engine: ValidationEngine, mutate, mutation: Mutation
    ) -> None:
        """explain/ is grounded in rule_text, so a blank one is ungroundable."""
        finding = next(
            f
            for f in engine.findings(mutate(mutation), mutation.syntax)
            if f.rule_id == mutation.rule_id
        )
        assert finding.rule_text.strip()
        assert finding.xpath.strip()


class TestBothSyntaxesAreCovered:
    @pytest.mark.parametrize("syntax", list(Syntax))
    def test_each_syntax_has_mutations(self, syntax: Syntax) -> None:
        assert len([m for m in MUTATIONS if m.syntax is syntax]) >= 5

    def test_the_same_rule_is_proven_in_both_syntaxes(self) -> None:
        """A rule caught in UBL but not CII is caught for half of production."""
        by_syntax = {s: {m.rule_id for m in MUTATIONS if m.syntax is s} for s in Syntax}
        assert by_syntax[Syntax.UBL] & by_syntax[Syntax.CII]
