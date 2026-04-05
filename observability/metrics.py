from __future__ import annotations

from dataclasses import dataclass, field

from utils.serialization import JsonDataclassMixin


@dataclass(slots=True)
class MetricsCollector(JsonDataclassMixin):
    counters: dict[str, int] = field(default_factory=dict)

    def increment(self, name: str, amount: int = 1) -> None:
        self.counters[name] = int(self.counters.get(name, 0)) + amount

    def get(self, name: str) -> int:
        return int(self.counters.get(name, 0))

    def snapshot(self) -> dict[str, int]:
        return dict(self.counters)
