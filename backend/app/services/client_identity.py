"""Comparable customer names are hints, never sufficient proof of identity."""


def normalized_client_name(value: str) -> str:
    return " ".join(value.split()).casefold()
