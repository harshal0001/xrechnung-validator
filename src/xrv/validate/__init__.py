"""XSD and Schematron execution, and the SVRL it produces.

Owns running the rules. Does not format anything for a human — that is `explain/`.
"""

from xrv.validate.engine import Evaluation, ValidationEngine, ValidationError
from xrv.validate.scenario import ScenarioMatcher
from xrv.validate.structure import StructureValidator
from xrv.validate.svrl import SVRL_NS, SvrlError, parse_svrl

__all__ = [
    "SVRL_NS",
    "Evaluation",
    "ScenarioMatcher",
    "StructureValidator",
    "SvrlError",
    "ValidationEngine",
    "ValidationError",
    "parse_svrl",
]
