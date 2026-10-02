"""Off-chain names for the on-chain uint8 category keys used by BudgetEnforcer.

An unset category has a 0 daily limit on-chain (fail-closed), so a new category must be registered with setCategoryDailyLimit before the agent can pay into it.
"""
from enum import IntEnum


class Category(IntEnum):
    DATA_ORACLE = 0
    INFRASTRUCTURE = 1  # domains, hosting, SaaS
