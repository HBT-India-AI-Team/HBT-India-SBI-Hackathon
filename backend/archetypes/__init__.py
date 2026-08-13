"""The archetype registry — see base.py for the Archetype contract. Importing
this package registers every archetype as a side effect (mirrors
agent_platform/stages/__init__.py's stage-registration pattern), so any code
that needs ARCHETYPES populated just needs to import from here.
"""
from .base import ARCHETYPES, Archetype, get_archetype, list_archetypes, register_archetype
from . import qualification  # noqa: F401 (import registers the archetype)
from . import conversational  # noqa: F401 (import registers the archetype)
from . import dialogue  # noqa: F401 (import registers the archetype)

__all__ = ["ARCHETYPES", "Archetype", "get_archetype", "list_archetypes", "register_archetype"]
