"""Caching of each user's grants (role assignments + permission codes).

Keys carry a global "access version". Anything that can change many users' access at once
(a role's permissions, the permission catalogue, a company being deactivated) bumps the
version, which retires every cached entry at once. A change to one user's role assignments
deletes just that user's entry. Invalidation runs after the transaction commits, so a
concurrent request can never re-cache data that is about to change.
"""

from django.core.cache import cache
from django.db import transaction

VERSION_KEY = "access:version"
TTL_SECONDS = 60 * 60  # safety net; entries are normally invalidated explicitly


def _version() -> int:
    version = cache.get(VERSION_KEY)
    if version is None:
        cache.add(VERSION_KEY, 1, timeout=None)
        version = cache.get(VERSION_KEY, 1)
    return version


def grants_key(user_id: int) -> str:
    return f"access:grants:v{_version()}:{user_id}"


def get_cached_grants(user_id: int):
    return cache.get(grants_key(user_id))


def set_cached_grants(user_id: int, grants) -> None:
    cache.set(grants_key(user_id), grants, timeout=TTL_SECONDS)


def _bump_now() -> None:
    try:
        cache.incr(VERSION_KEY)
    except ValueError:  # key missing (evicted or first run)
        cache.set(VERSION_KEY, 2, timeout=None)


def invalidate_all() -> None:
    """Every user's access may have changed. Runs only if the change commits, so a rolled-back
    change can never leave its effect in the cache."""
    transaction.on_commit(_bump_now)


def invalidate_user(user_id: int) -> None:
    transaction.on_commit(lambda: cache.delete(grants_key(user_id)))
