"""Ядро диагностики: каталог правил, вычислитель и сервис гистерезиса."""

from .catalog import RuleCatalog, load_catalog
from .evaluator import RuleEvaluation, evaluate_all, evaluate_rule
from .service import DiagnosticsService

__all__ = [
    "DiagnosticsService",
    "RuleCatalog",
    "RuleEvaluation",
    "evaluate_all",
    "evaluate_rule",
    "load_catalog",
]
