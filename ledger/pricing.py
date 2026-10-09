"""Cost = tokens x per-model list price. Cache reads and writes priced separately."""
import json
import re
from pathlib import Path

_DATE_SUFFIX = re.compile(r"-\d{8}$")
_PROVIDER_PREFIX = re.compile(r"^(us\.|eu\.|global\.)?(anthropic\.|openai\.)")


class PriceTable:
    def __init__(self, path: Path):
        data = json.loads(Path(path).read_text())
        self.models = data["models"]
        self.aliases = data.get("aliases", {})
        self.ignore = set(data.get("ignore", []))
        self.unknown: set[str] = set()

    def normalize(self, model: str) -> str:
        m = _PROVIDER_PREFIX.sub("", model or "")
        m = _DATE_SUFFIX.sub("", m)
        m = m.replace("-v1:0", "")
        return self.aliases.get(m, m)

    def is_ignored(self, model: str) -> bool:
        return (model or "") in self.ignore

    def price(self, model: str):
        return self.models.get(self.normalize(model))

    def cost_usd(self, model: str, input_tok: int, output_tok: int,
                 cache_write_5m: int, cache_write_1h: int, cache_read: int):
        """Return cost in USD, or None if the model has no price."""
        p = self.price(model)
        if p is None:
            self.unknown.add(self.normalize(model))
            return None
        per = 1_000_000
        return (input_tok * p["input"] + output_tok * p["output"]
                + cache_write_5m * p["cache_write_5m"] + cache_write_1h * p["cache_write_1h"]
                + cache_read * p["cache_read"]) / per
