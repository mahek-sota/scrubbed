"""Identifier categories: a subset of the HIPAA Safe Harbor list.

This benchmark measures detection of these categories. It is not a compliance tool.
"""

CATEGORIES: tuple[str, ...] = (
    "NAME",
    "DATE",
    "AGE_OVER_89",
    "PHONE",
    "EMAIL",
    "MRN",
    "SSN",
    "ADDRESS",
    "CITY",
    "ZIP",
    "HOSPITAL",
)

# Label for a predicted span whose type has no counterpart in CATEGORIES.
OTHER = "OTHER"
