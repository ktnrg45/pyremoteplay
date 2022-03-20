"""FEC Utils."""


def aligned_size(size: int) -> int:
    """Return Aligned Size. Size should be divisible by 16."""
    return ((size + 15) // 16) * 16
