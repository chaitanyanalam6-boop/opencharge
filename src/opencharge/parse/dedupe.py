"""Collapse item-level prices that a file repeats.

All three CMS shapes can repeat the same item-level price: tall files copy gross/cash onto
every payer row, and wide/JSON files list an item several times when payers' rates differ
(CMS's own samples do this). Each distinct (item, price type, value) is emitted once,
attributed to the first row it appears on, so its provenance points at a real cell.
"""

import hashlib
from collections import Counter
from collections.abc import Mapping

from opencharge.parse.records import PriceType


class FirstSeen:
    def __init__(self, quirks: Counter[str]) -> None:
        # 16-byte digests keep memory small even for multi-GB files; unlike hash(), a
        # collision (which would silently drop a real price) is not a realistic concern.
        self._seen: set[bytes] = set()
        self._quirks = quirks

    def is_first(self, item: Mapping[str, object], price_type: PriceType, value: str) -> bool:
        key = hashlib.blake2b(repr((item, price_type, value)).encode(), digest_size=16).digest()
        if key in self._seen:
            self._quirks["repeated_item_price_collapsed"] += 1
            return False
        self._seen.add(key)
        return True
