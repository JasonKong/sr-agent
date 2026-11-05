"""When is a difference between two runs real? (DESIGN.md section 7.5)

Two runs on the same cases are compared case by case: a case is *fixed* when the newer
run is right and the older wrong, *broke* when the reverse. Cases right or wrong in both
say nothing about the change, and a case with an error in either run (an outage, not an
answer) is left out. Under "no difference", fixed and broke are equally likely, so the
exact two-sided sign test (McNemar's exact test) gives how surprising the split is:

- **better** or **worse**: p < 0.05;
- **possibly better** or **possibly worse**: p < 0.20, which takes at least four more cases
  one way than the other. Worth a second run of both before acting on it, if the decision
  matters;
- **within noise**: anything else.

On 75 cases a run must fix six to ten cases more than it breaks to count as better: a
change of two to four points is not measurable at this size. Rerunning one configuration
(TE5) checks the rule: the two runs must come out "within noise".
"""

from __future__ import annotations

from math import comb

REAL_P = 0.05
POSSIBLE_P = 0.20


def sign_test(fixed: int, broke: int) -> float:
    """Two-sided exact p of a split at least this uneven, if either way were equally likely."""
    n = fixed + broke
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(min(fixed, broke) + 1))
    return min(1.0, 2 * tail / (1 << n))  # 1 << n is 2 to the n, an int for mypy


def verdict(fixed: int, broke: int) -> str:
    p = sign_test(fixed, broke)
    direction = "better" if fixed > broke else "worse"
    if p < REAL_P:
        return direction
    if p < POSSIBLE_P:
        return f"possibly {direction}"
    return "within noise"
