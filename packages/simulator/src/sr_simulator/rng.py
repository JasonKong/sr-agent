"""Independent, reproducible random streams.

Each entity draws from its own stream, derived from the seed and the entity's identity.
Adding draws in one place therefore never shifts the data generated anywhere else, so a
change such as injecting a failure into one referral leaves the other referrals as they were.
"""

import hashlib
import json
import random


def derive_seed(seed: int, *parts: str | int) -> int:
    """A 64-bit seed unique to `seed` and `parts` (JSON-encoded, so parts never run together)."""
    payload = json.dumps([seed, *parts]).encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def rng_for(seed: int, *parts: str | int) -> random.Random:
    return random.Random(derive_seed(seed, *parts))
