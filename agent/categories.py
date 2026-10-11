"""Per-business spending categories.

On-chain a category is a uint8 key with a daily limit set by the owner (setCategoryDailyLimit). An unset category has a
limit of 0, so the contract refuses it (fail closed). Off-chain, each business keeps its own names for those keys in the
`categories` table; this module holds the shared rules.
"""
from typing import Optional

MAX_ID = 255  # uint8
MAX_NAME_LENGTH = 40

# The original business already used 0 and 1 on its contract, so they are seeded to keep its history readable.
HOME_SEED = ((0, "Data Oracle"), (1, "Infrastructure"))


def clean_name(text: Optional[str]) -> str:
    """One line of plain text, 1 to 40 characters, no control characters. Raises ValueError otherwise."""
    name = " ".join(str(text or "").split())
    if not name or len(name) > MAX_NAME_LENGTH or any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise ValueError(f"A category name is 1 to {MAX_NAME_LENGTH} characters of plain text")
    return name


def usable(limit_units: Optional[int]) -> Optional[bool]:
    """A category can take purchase orders and invoices only while its on-chain daily limit is above zero.
    None means the limit could not be read, which is never treated as usable."""
    if limit_units is None:
        return None
    return limit_units > 0
