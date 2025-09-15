def prune(items: list) -> list:
    """Remove all None items from an array and return the filtered array."""
    return [item for item in items if item is not None]