"""Building a :class:`Directory` from a loaded definition.

Kept apart from :mod:`.principals` so the authorisation model has no dependency on how a
factory happens to be stored. The direction of the dependency is the point: a `Directory`
is a thing you can build in a test with three lines, and the loader is what makes the
production one come from files a reviewer can read.
"""

from __future__ import annotations

from software_factory.definition.loader import Definition
from software_factory.identity.principals import (
    Capability,
    Directory,
    Principal,
    PrincipalKind,
)


def directory_from(definition: Definition) -> Directory:
    """Build the factory's principal directory.

    Unknown capability names are dropped rather than raising: `validate` reports them as
    errors against the file and the line, which is a better place for a reader to meet the
    problem than a traceback. Dropping is the safe direction -- an unrecognised name grants
    nothing.
    """
    directory = Directory()
    for loaded in definition.principals.values():
        declared = loaded.definition
        capabilities = frozenset(
            Capability(name) for name in declared.capabilities if name in set(Capability)
        )
        directory.add(
            Principal(
                id=declared.id,
                kind=PrincipalKind(declared.kind),
                display_name=declared.display_name or declared.id,
                groups=frozenset(declared.groups),
                capabilities=capabilities,
                identities=frozenset(identity.lower() for identity in declared.identities),
                active=declared.active,
            )
        )
    return directory
