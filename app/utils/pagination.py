"""Pagination — limite la charge mémoire et les temps de réponse."""


def clamp_page(
    limit: int | None = None,
    offset: int | None = None,
    *,
    default: int = 50,
    maximum: int = 200,
) -> tuple[int, int]:
    try:
        page_limit = int(limit) if limit is not None else default
    except (TypeError, ValueError):
        page_limit = default
    try:
        page_offset = int(offset) if offset is not None else 0
    except (TypeError, ValueError):
        page_offset = 0
    page_limit = max(1, min(page_limit, maximum))
    page_offset = max(0, page_offset)
    return page_limit, page_offset
