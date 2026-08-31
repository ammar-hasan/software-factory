"""Building a digest from several parts, unambiguously.

Joining parts with a separator is the obvious way and is wrong whenever a part can contain
the separator: ``("a|b",)`` and ``("a", "b")`` join to the same string and therefore hash to
the same value. For a display string that is untidy. For an *identity* -- an event id, a
deduplication fingerprint, a failure signature -- it is a collision anyone who controls one
part can produce deliberately, which turns "this event was already handled" into a way of
suppressing an event that was not.

Length-prefixing each part is injective: the decoder can always tell where one part ends, so
no two distinct sequences produce the same material. It costs a few bytes and removes the
whole class of problem.
"""

from __future__ import annotations

import hashlib


def digest_parts(*parts: str, length: int = 24) -> str:
    """A hex digest over an unambiguous encoding of ``parts``.

    ``length`` truncates the hex output. These are identities within one factory's store,
    not cryptographic commitments against an adversary with unlimited attempts, so the
    truncation trades collision margin for something a human can read in a log line. 24 hex
    characters is 96 bits: a factory would need on the order of 2^48 events for an accidental
    collision to be likely, which is not a number of events anything here will produce.
    """
    material = "".join(f"{len(part)}:{part}" for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:length]
