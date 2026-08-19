"""The Role Engine: behaviour as data.

`schema` defines what a Role Pack may say, `loader` reads one from disk, and
`prompt_builder` turns it into the layered system prompt the LLM sees. Core pipeline
code imports only these three names and never branches on a role id.
"""

from backend.roles.loader import list_roles, load_role, load_role_cached
from backend.roles.prompt_builder import PromptLayer, build_system_prompt, compose_messages
from backend.roles.schema import RolePack

__all__ = [
    "RolePack",
    "load_role",
    "load_role_cached",
    "list_roles",
    "build_system_prompt",
    "compose_messages",
    "PromptLayer",
]
