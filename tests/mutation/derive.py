"""Derive mutations from the rule set instead of writing them by hand.

The hand-written catalogue proves a rule fires by someone reading the rule,
finding the element it guards and deleting it. That does not scale past a few
dozen rules, and it proves only the rules somebody thought to write down.

Every assertion in the compiled stylesheets already names what it reads: the
`match` of its template is the context, and its `test` is an XPath over that
context. So the elements a rule depends on can be read out of the rule. This
module does that, proposes single edits to those elements in a valid reference
invoice — remove it, empty it, duplicate it, give it a value the rule's own
literals suggest — and runs each edited document through the stylesheets. An
edit that makes a rule fire which did not fire before is a mutation for that
rule, and what else fired alongside it is its collateral.

Nothing here decides whether a rule *should* fire. The expressions are read
with regular expressions, not an XPath parser, so the reading only has to be
good enough to propose candidates: the stylesheets are the judge of every one.
A wrong guess costs a wasted run, never a wrong result.
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from pathlib import Path

from lxml import etree

from mutation.catalogue import NAMESPACES as _CATALOGUE_NAMESPACES
from mutation.catalogue import MutationError
from xrv.core import Syntax

XSL = "http://www.w3.org/1999/XSL/Transform"
SVRL = "http://purl.oclc.org/dsdl/svrl"

#: Prefixes used to write element paths in the derived file. A path spelled
#: `cac:Party/cbc:EndpointID` is a tenth the length of its expanded form, and
#: the file is meant to be read in a diff.
NAMESPACES = {
    **_CATALOGUE_NAMESPACES,
    "qdt": "urn:un:unece:uncefact:data:standard:QualifiedDataType:100",
    "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
}
_PREFIX_OF = {uri: prefix for prefix, uri in NAMESPACES.items()}

#: Business rules. The `UBL-*` and `CII-*` syntax-binding rules are left out
#: here for the reason the explanation catalogue leaves them out: almost all of
#: them say an element the syntax allows must not be used, which no edit to a
#: valid invoice short of inventing content can breach.
TARGET_PREFIX = "BR-"


# --------------------------------------------------------------------------- #
# Reading the rule set
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Assertion:
    """One `svrl:failed-assert` and the template it sits in."""

    rule_id: str
    flag: str
    context: str
    test: str


@dataclass(frozen=True)
class Stylesheet:
    """What one compiled Schematron says, without running it."""

    assertions: tuple[Assertion, ...]
    #: `xsl:variable` selects by name. A Schematron `let` compiles to one, and a
    #: test that reads `$BT-31orBT-32Path` depends on whatever that selects.
    variables: Mapping[str, tuple[str, ...]]
    namespaces: Mapping[str, str]


def read_stylesheet(path: Path) -> Stylesheet:
    tree = etree.parse(str(path))
    root = tree.getroot()

    variables: dict[str, list[str]] = {}
    for variable in root.iter(f"{{{XSL}}}variable"):
        name, select = variable.get("name"), variable.get("select")
        if name and select and select not in variables.setdefault(name, []):
            variables[name].append(select)

    assertions = []
    for failed in root.iter(f"{{{SVRL}}}failed-assert"):
        rule_id = (failed.get("id") or "").strip()
        template = next(failed.iterancestors(f"{{{XSL}}}template"), None)
        test = failed.find(f"{{{XSL}}}attribute[@name='test']")
        if not rule_id or template is None:
            continue
        assertions.append(
            Assertion(
                rule_id=rule_id,
                flag=(failed.get("flag") or "").strip(),
                context=template.get("match") or "",
                test="".join(test.itertext()) if test is not None else failed.get("test") or "",
            )
        )

    return Stylesheet(
        assertions=tuple(assertions),
        variables={name: tuple(selects) for name, selects in variables.items()},
        namespaces={prefix: uri for prefix, uri in root.nsmap.items() if prefix},
    )


# --------------------------------------------------------------------------- #
# Reading an expression
# --------------------------------------------------------------------------- #

_LITERAL = re.compile(r"'([^']*)'|\"([^\"]*)\"")
_VARIABLE = re.compile(r"\$([A-Za-z_][\w.-]*)")
_NAME = r"[A-Za-z_][\w.-]*"
_STEP = rf"(?:@?{_NAME}:{_NAME}|@{_NAME})"
_PATH = rf"{_STEP}(?:\s*/\s*{_STEP})*"
#: A path, and not the front half of a longer name or a function call.
_CHAIN = re.compile(rf"(?<![\w:.$@-])({_PATH})(?![\w:.-])(?!\s*\()")
_PREDICATE = re.compile(r"\[([^\[\]]*)\]")
_OWNER = re.compile(rf"({_STEP})\s*$")
_CHILD_AXIS = re.compile(r"\bchild::")
_OTHER_AXIS = re.compile(
    r"\b(?:ancestor-or-self|ancestor|descendant-or-self|descendant|following-sibling|"
    r"preceding-sibling|following|preceding|parent|self)::"
)
#: Functions that hand back the node they were given, or a value read from it.
#: `cac:TaxTotal/xs:decimal(cbc:TaxAmount)` is a path to `cbc:TaxAmount` with a
#: cast in the middle of it, and has to be read as one path to find the node.
_TRANSPARENT = re.compile(
    r"(?<![\w:.-])(?:xs:[A-Za-z]+|normalize-space|upper-case|lower-case|string|number|round|abs|"
    r"exists|boolean|count|sum|string-length)\s*\(\s*((?:\.\./|//|/)?" + _PATH + r")\s*\)"
)
_NAME_SUFFIX = re.compile(r"ends-with\(\s*(?:local-)?name\(\)\s*,\s*'(\w+)'\s*\)")

#: Longer literals are code lists spelled as one space-separated string, or
#: regular expressions. Neither is a value to try in a field.
_MAX_LITERAL = 24
_MAX_LITERALS = 16
_EXPANSION_DEPTH = 4


def expand(expression: str, variables: Mapping[str, tuple[str, ...]]) -> str:
    """Inline `$variable` references, so what they select is read too.

    Unknown names are left as they are: `for` and `every` bind their own, and
    those are not declared anywhere to look up.
    """
    for _ in range(_EXPANSION_DEPTH):
        expanded = _VARIABLE.sub(
            lambda m: (
                "(" + " , ".join(variables[m.group(1)]) + ")"
                if m.group(1) in variables
                else m.group(0)
            ),
            expression,
        )
        if expanded == expression:
            break
        expression = expanded
    return expression


def literals(expression: str) -> tuple[str, ...]:
    """String literals worth trying as a field value, in order of appearance."""
    found: list[str] = []
    for match in _LITERAL.finditer(expression):
        value = match.group(1) if match.group(1) is not None else match.group(2)
        usable = value and len(value) <= _MAX_LITERAL and not re.search(r"\s", value)
        if usable and value not in found:
            found.append(value)
    return tuple(found[:_MAX_LITERALS])


def name_suffixes(expression: str) -> tuple[str, ...]:
    """Suffixes from `ends-with(name(), 'Amount')` — a context with no path in it."""
    found = []
    for match in _NAME_SUFFIX.finditer(expression):
        if match.group(1) not in found:
            found.append(match.group(1))
    return tuple(found)


@dataclass(frozen=True)
class Chain:
    """A run of child steps, as written: `cac:Party/cbc:EndpointID`."""

    steps: tuple[str, ...]
    #: False when the path was anchored (`/`, `//`, `../`, or another axis), so
    #: it does not start at the context node and must not be joined onto it.
    relative: bool


def _blank(expression: str) -> str:
    """Empty every string literal. Their contents hold brackets and colons that
    would otherwise be read as predicates and names."""
    return _LITERAL.sub("''", expression)


def _normalise(expression: str) -> str:
    expression = _CHILD_AXIS.sub("", expression)
    expression = _OTHER_AXIS.sub("//", expression)
    while True:
        unwrapped = _TRANSPARENT.sub(r" \1 ", expression)
        if unwrapped == expression:
            return expression
        expression = unwrapped


def _chains(expression: str) -> Iterator[Chain]:
    for match in _CHAIN.finditer(expression):
        before = expression[: match.start()].rstrip()
        steps = tuple(step.strip() for step in match.group(1).split("/"))
        yield Chain(steps=steps, relative=not before.endswith("/"))


def read_expression(expression: str) -> tuple[tuple[Chain, ...], tuple[Chain, ...]]:
    """Split an expression into the paths it walks and the paths its predicates test.

    A predicate's paths are relative to the step the predicate hangs on, so each
    is returned with that step in front: `cac:TaxCategory[cbc:ID = 'S']` yields
    `cac:TaxCategory` and `cac:TaxCategory/cbc:ID`.
    """
    remaining = _blank(expression)
    inside: list[Chain] = []
    while True:
        predicate = _PREDICATE.search(remaining)
        if predicate is None:
            break
        before = remaining[: predicate.start()]
        owner = _OWNER.search(before)
        for chain in _chains(_normalise(predicate.group(1))):
            steps = (owner.group(1), *chain.steps) if chain.relative and owner else chain.steps
            inside.append(Chain(steps, relative=False))
        remaining = before + remaining[predicate.end() :]
    return tuple(_chains(_normalise(remaining))), tuple(inside)


class Shape(StrEnum):
    """Roughly what kind of test an assertion makes.

    A reading aid for the list of rules no edit reached — it says what sort of
    edit would be needed — and nothing more. No mutation is chosen by it.
    """

    PRESENCE = "presence"
    CODE_LIST = "code-list"
    PATTERN = "pattern"
    ARITHMETIC = "arithmetic"
    COMPARISON = "comparison"
    PROHIBITION = "prohibition"
    OTHER = "other"


def classify(test: str) -> Shape:
    lists = [len((m.group(1) or m.group(2) or "").split()) > 1 for m in _LITERAL.finditer(test)]
    blank = " ".join(_blank(test).split())

    if re.fullmatch(rf"not\(\s*(?:exists\()?\s*{_PATH}\s*\)?\s*\)", blank) or re.fullmatch(
        rf"count\(\s*{_PATH}\s*\)\s*(?:<=|<|=|le|lt|eq)\s*[01]", blank
    ):
        return Shape.PROHIBITION
    if re.search(r"\bmatches\s*\(", blank):
        return Shape.PATTERN
    if re.search(r"\b(?:sum|round)\s*\(", blank) or re.search(r"\)\s*[-+*]\s*\(?|\bdiv\b", blank):
        return Shape.ARITHMETIC
    # A list is spelled either as one space-separated string searched with
    # contains(), or as a sequence of strings compared with `=`.
    if (any(lists) and "contains(" in blank) or re.search(r"=\s*\(\s*''\s*,", blank):
        return Shape.CODE_LIST
    if re.search(r"<=|>=|<|>|\b(?:lt|gt|le|ge)\b", blank):
        return Shape.COMPARISON
    if re.search(r"\bexists\s*\(|!=\s*''|\bboolean\s*\(", blank) or re.fullmatch(_PATH, blank):
        return Shape.PRESENCE
    return Shape.OTHER


# --------------------------------------------------------------------------- #
# From a rule to the nodes it reads
# --------------------------------------------------------------------------- #

Names = tuple[str, ...]


@dataclass(frozen=True)
class Target:
    """Somewhere a rule looks, as element names in Clark notation.

    `anchored` are the path joined onto the rule's context — the precise
    reading. `loose` is the path alone, used only when no anchored form matches
    anything, which is what happens when the test climbs out of its context
    with `../` and the join is therefore wrong.
    """

    anchored: tuple[Names, ...]
    loose: Names
    #: How many enclosing elements the rule's own path names. Those are fair
    #: game for removal too: a rule reading `cac:Contact/cbc:Telephone` is
    #: broken by removing the contact as surely as by removing the telephone.
    enclosing: int = 0


@dataclass(frozen=True)
class Plan:
    """Everything one rule reads, merged across the assertions that carry its id."""

    rule_id: str
    targets: tuple[Target, ...]
    literals: tuple[str, ...]
    suffixes: tuple[str, ...]
    contexts: frozenset[str]
    shape: Shape


def _clark(steps: Iterable[str], namespaces: Mapping[str, str]) -> Names | None:
    """Resolve prefixes. None when a step is not an element this document has —
    `xs:date` left over from a cast, say."""
    resolved = []
    for step in steps:
        if step.startswith("@"):
            resolved.append("@" + step[1:].rpartition(":")[2])
            continue
        prefix, _, local = step.partition(":")
        if prefix not in namespaces:
            return None
        resolved.append(f"{{{namespaces[prefix]}}}{local}")
    if any(name.startswith("@") for name in resolved[:-1]):
        return None
    return tuple(resolved)


def _plan(assertion: Assertion, sheet: Stylesheet) -> Plan:
    context = expand(assertion.context, sheet.variables)
    test = expand(assertion.test, sheet.variables)
    targets: list[Target] = []

    context_paths, context_predicates = read_expression(context)
    tails = []
    for chain in context_paths:
        names = _clark(chain.steps, sheet.namespaces)
        if names is not None:
            tails.append(names)
            targets.append(Target(anchored=(), loose=names))
    for chain in context_predicates:
        names = _clark(chain.steps, sheet.namespaces)
        if names is not None:
            targets.append(Target(anchored=(), loose=names))

    test_paths, test_predicates = read_expression(test)
    for chain in test_paths:
        names = _clark(chain.steps, sheet.namespaces)
        if names is None:
            continue
        anchored = tuple(tail + names for tail in tails) if chain.relative else ()
        targets.append(Target(anchored=anchored, loose=names, enclosing=len(names) - 1))
    for chain in test_predicates:
        names = _clark(chain.steps, sheet.namespaces)
        if names is not None:
            targets.append(Target(anchored=(), loose=names, enclosing=len(names) - 2))

    found = literals(context) + tuple(v for v in literals(test) if v not in literals(context))
    return Plan(
        rule_id=assertion.rule_id,
        targets=tuple(dict.fromkeys(targets)),
        literals=found[:_MAX_LITERALS],
        suffixes=name_suffixes(context),
        contexts=frozenset({assertion.context}),
        shape=classify(assertion.test),
    )


def plans(sheets: Iterable[Stylesheet]) -> dict[str, Plan]:
    """One plan per business rule, for the stylesheets of one syntax."""
    merged: dict[str, Plan] = {}
    for sheet in sheets:
        for assertion in sheet.assertions:
            if not assertion.rule_id.startswith(TARGET_PREFIX):
                continue
            plan = _plan(assertion, sheet)
            earlier = merged.get(plan.rule_id)
            if earlier is not None:
                plan = Plan(
                    rule_id=plan.rule_id,
                    targets=tuple(dict.fromkeys(earlier.targets + plan.targets)),
                    literals=tuple(dict.fromkeys(earlier.literals + plan.literals))[:_MAX_LITERALS],
                    suffixes=tuple(dict.fromkeys(earlier.suffixes + plan.suffixes)),
                    contexts=earlier.contexts | plan.contexts,
                    shape=earlier.shape,
                )
            merged[plan.rule_id] = plan
    return merged


# --------------------------------------------------------------------------- #
# Edits
# --------------------------------------------------------------------------- #


class Op(StrEnum):
    DELETE = "delete"
    SET = "set"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class Edit:
    """One change to one node of one document."""

    #: ElementPath from the root element, written with the prefixes in NAMESPACES.
    path: str
    op: Op
    attribute: str | None = None
    value: str | None = None

    def describe(self) -> str:
        where = self.path if self.attribute is None else f"{self.path}/@{self.attribute}"
        if self.op is Op.SET:
            return f"{where} set to {self.value!r}"
        return f"{where} {'removed' if self.op is Op.DELETE else 'repeated'}"

    def apply(self, document: bytes) -> bytes:
        """Return the document with this edit made.

        Raises when the edit would change nothing, for the reason the
        hand-written catalogue does: a mutation that does not mutate turns its
        test into an assertion about a valid invoice.
        """
        tree = etree.parse(BytesIO(document))
        node = tree.getroot().find(self.path, namespaces=NAMESPACES)
        if node is None:
            raise MutationError(f"{self.path!r} matched nothing — the base document changed shape")

        if self.attribute is not None:
            if self.attribute not in node.attrib:
                raise MutationError(f"{self.path!r} has no attribute {self.attribute!r}")
            if self.op is Op.DELETE:
                del node.attrib[self.attribute]
            elif self.op is Op.SET and node.get(self.attribute) != self.value:
                node.set(self.attribute, self.value or "")
            else:
                raise MutationError(f"{self.describe()} changes nothing")
        elif self.op is Op.DELETE:
            node.getparent().remove(node)
        elif self.op is Op.DUPLICATE:
            twin = copy.deepcopy(node)
            twin.tail = node.tail
            node.addnext(twin)
        elif len(node) or (node.text or "") == (self.value or ""):
            raise MutationError(f"{self.describe()} changes nothing")
        else:
            node.text = self.value

        return etree.tostring(tree, xml_declaration=True, encoding="UTF-8")


_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_DATE = re.compile(r"\d{4}-?\d{2}-?\d{2}")

#: Tried in any field holding a number. Each crosses a different line: a total
#: that no longer adds up, a sign, a third decimal place, and zero.
_WRONG_NUMBERS = ("999.99", "-1", "1.234", "0")
#: Tried in any other field: in no code list, and an identifier with no prefix.
_WRONG_TEXT = ("ZZZ", "123456789")

#: How many nodes one path may select in one document. Invoice lines repeat the
#: same structure, and the fifth line teaches nothing the first four did not.
_MAX_NODES = 4


def _values(current: str, suggested: Iterable[str]) -> list[str]:
    current = current.strip()
    values = [""]
    if current in ("true", "false"):
        values.append("false" if current == "true" else "true")
    elif _NUMBER.fullmatch(current):
        values.extend(_WRONG_NUMBERS)
    elif not _DATE.fullmatch(current):
        values.extend(_WRONG_TEXT)
    values.extend(suggested)
    return [value for value in dict.fromkeys(values) if value != current]


class DocumentIndex:
    """A parsed invoice, indexed so a rule's paths can be matched against it."""

    def __init__(self, document: bytes) -> None:
        self._tree = etree.parse(BytesIO(document))
        self._root = self._tree.getroot()
        self._names: dict[etree._Element, Names] = {}
        self._by_name: dict[str, list[etree._Element]] = {}
        for element in self._root.iter():
            if not isinstance(element.tag, str):
                continue
            parent = element.getparent()
            names = (*self._names[parent], element.tag) if parent is not None else (element.tag,)
            self._names[element] = names
            self._by_name.setdefault(element.tag, []).append(element)

    def _path(self, element: etree._Element) -> str | None:
        """The element's path in prefixed form, or None if it cannot be written."""
        if element is self._root:
            return None
        clark = self._tree.getelementpath(element)
        unknown = False

        def prefixed(match: re.Match[str]) -> str:
            nonlocal unknown
            prefix = _PREFIX_OF.get(match.group(1))
            unknown = unknown or prefix is None
            return f"{prefix}:"

        path = re.sub(r"\{([^}]*)\}", prefixed, clark)
        return None if unknown else path

    def _select(self, names: Names) -> list[tuple[etree._Element, str | None]]:
        attribute = names[-1][1:] if names[-1].startswith("@") else None
        elements = names[:-1] if attribute else names
        if not elements:
            pool = [e for e in self._names if attribute in e.attrib]
        else:
            pool = [
                e
                for e in self._by_name.get(elements[-1], [])
                if self._names[e][-len(elements) :] == elements
                and (attribute is None or attribute in e.attrib)
            ]
        return [(element, attribute) for element in pool[:_MAX_NODES]]

    def edits(self, plan: Plan) -> Iterator[Edit]:
        """Every single edit this rule's own reading of the document suggests."""
        for target in plan.targets:
            selected = [hit for names in target.anchored for hit in self._select(names)]
            if not selected:
                selected = self._select(target.loose)
            for element, attribute in selected:
                yield from self._edits_at(element, attribute, plan.literals)
                enclosing = element
                for _ in range(max(target.enclosing, 0)):
                    enclosing = enclosing.getparent()
                    if enclosing is None or enclosing is self._root:
                        break
                    yield from self._edits_at(enclosing, None, ())

        for suffix in plan.suffixes:
            seen: set[str] = set()
            for element in self._names:
                local = etree.QName(element).localname
                if local.endswith(suffix) and not len(element) and element.tag not in seen:
                    seen.add(element.tag)
                    yield from self._edits_at(element, None, plan.literals)

    def _edits_at(
        self, element: etree._Element, attribute: str | None, suggested: Iterable[str]
    ) -> Iterator[Edit]:
        path = self._path(element)
        if path is None:
            return
        if attribute is not None:
            yield Edit(path, Op.DELETE, attribute)
            for value in _values(element.get(attribute) or "", (*_WRONG_TEXT, *suggested)):
                yield Edit(path, Op.SET, attribute, value)
            return
        yield Edit(path, Op.DELETE)
        yield Edit(path, Op.DUPLICATE)
        if not len(element):
            for value in _values(element.text or "", suggested):
                yield Edit(path, Op.SET, value=value)


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Derived:
    """A mutation the stylesheets confirmed: this edit makes this rule fire."""

    rule_id: str
    syntax: Syntax
    #: Reference message, relative to the test suite's `instances` directory.
    base: str
    edit: Edit
    #: Every other rule the same edit trips. Recorded, not chosen: it is what
    #: the stylesheets reported for this edit when the file was derived.
    collateral: frozenset[str] = frozenset()
    #: True when the XSD rejects the edited document, so the integrated pipeline
    #: stops at the structural failure and this rule is reached only when the
    #: rule layer is run on its own.
    caught_by_schema: bool = False

    @property
    def expected(self) -> frozenset[str]:
        return self.collateral | {self.rule_id}

    @property
    def name(self) -> str:
        return f"{self.rule_id}-{self.syntax}"

    def rank(self) -> tuple[object, ...]:
        """Lower is a better witness for the rule.

        A mutation the schema lets through shows the rule firing in the pipeline
        a user actually gets, and one with no collateral shows it firing for
        exactly one reason. After that, the plainest edit wins, and the rest of
        the key only makes the choice repeatable.
        """
        return (
            self.caught_by_schema,
            len(self.collateral),
            (Op.DELETE, Op.SET, Op.DUPLICATE).index(self.edit.op),
            len(self.edit.value or ""),
            self.base,
            self.edit.path,
            self.edit.attribute or "",
            self.edit.value or "",
        )

    def to_json(self) -> dict[str, object]:
        record: dict[str, object] = {
            "rule_id": self.rule_id,
            "syntax": str(self.syntax),
            "base": self.base,
            "path": self.edit.path,
            "op": str(self.edit.op),
        }
        if self.edit.attribute is not None:
            record["attribute"] = self.edit.attribute
        if self.edit.value is not None:
            record["value"] = self.edit.value
        if self.collateral:
            record["collateral"] = sorted(self.collateral)
        if self.caught_by_schema:
            record["caught_by_schema"] = True
        return record

    @classmethod
    def from_json(cls, record: Mapping[str, object]) -> Derived:
        collateral = record.get("collateral", ())
        assert isinstance(collateral, list | tuple)
        return cls(
            rule_id=str(record["rule_id"]),
            syntax=Syntax(str(record["syntax"])),
            base=str(record["base"]),
            edit=Edit(
                path=str(record["path"]),
                op=Op(str(record["op"])),
                attribute=None if record.get("attribute") is None else str(record["attribute"]),
                value=None if record.get("value") is None else str(record["value"]),
            ),
            collateral=frozenset(str(rule) for rule in collateral),
            caught_by_schema=bool(record.get("caught_by_schema", False)),
        )


def newly_fired(before: Counter[str], after: Counter[str]) -> frozenset[str]:
    """Rules reported more often after an edit than before it.

    Counted, not compared as sets: a valid reference message already carries
    informational findings, and a rule that fires once there and twice after an
    edit has fired because of the edit.
    """
    return frozenset((after - before).keys())
