"""lint-agent: AI-powered linting that learns your codebase style."""

from .agent import LintAgent
from .learner import StyleLearner

__all__ = ["LintAgent", "StyleLearner"]
__version__ = "0.1.0"
