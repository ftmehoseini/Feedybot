"""Loading Role Packs from disk.

Deliberately boring: read one YAML file, validate it, return a frozen model. No plugin
discovery, no entry points, no dynamic imports. A role is data.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from backend.errors import ConfigError
from backend.roles.schema import RolePack

_ROLE_ID_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789_")


def _role_path(roles_dir: Path, role_id: str) -> Path:
    """Resolve a role id to a file, refusing anything that could escape `roles_dir`.

    Role ids come from configuration, which in a fleet deployment may come from a
    remote source. Treating them as untrusted path components costs nothing.
    """
    if not role_id or not set(role_id) <= _ROLE_ID_ALLOWED:
        raise ConfigError(
            f"invalid role id {role_id!r}: expected lowercase letters, digits and underscores"
        )
    candidate = (roles_dir / f"{role_id}.yaml").resolve()
    roles_root = roles_dir.resolve()
    if roles_root not in candidate.parents:
        raise ConfigError(f"role id {role_id!r} resolves outside the roles directory")
    return candidate


def load_role(role_id: str, roles_dir: Path) -> RolePack:
    """Load and validate one Role Pack.

    Raises:
        ConfigError: if the file is missing, unparseable, or fails schema validation.
            Role problems are configuration problems, so they surface at startup.
    """
    path = _role_path(roles_dir, role_id)
    if not path.is_file():
        available = ", ".join(sorted(list_roles(roles_dir))) or "(none)"
        raise ConfigError(f"role {role_id!r} not found at {path}; available roles: {available}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"role {role_id!r} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"role {role_id!r} must be a YAML mapping, got {type(raw).__name__}")

    try:
        role = RolePack(**raw)
    except Exception as exc:
        raise ConfigError(f"role {role_id!r} failed validation: {exc}") from exc

    if role.id != role_id:
        raise ConfigError(
            f"role file {path.name} declares id {role.id!r}; filename and id must match"
        )
    return role


def list_roles(roles_dir: Path) -> list[str]:
    """Every role id available in `roles_dir`, sorted. Never raises on a bad directory."""
    if not roles_dir.is_dir():
        return []
    return sorted(p.stem for p in roles_dir.glob("*.yaml"))


@lru_cache(maxsize=16)
def _cached_role(role_id: str, roles_dir: str, mtime_ns: int) -> RolePack:
    return load_role(role_id, Path(roles_dir))


def load_role_cached(role_id: str, roles_dir: Path) -> RolePack:
    """`load_role` with an mtime-keyed cache.

    Roles are read on every new connection; re-parsing YAML per connection is waste.
    Keying on mtime means editing a role during development still takes effect without
    a restart, which is the only reason this is not a plain `lru_cache`.
    """
    path = _role_path(roles_dir, role_id)
    mtime = path.stat().st_mtime_ns if path.is_file() else 0
    return _cached_role(role_id, str(roles_dir), mtime)
