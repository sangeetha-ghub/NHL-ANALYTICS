"""Build parameterised WHERE clauses from optional UI filters.

The dashboard pushes filtering into SQL (instead of loading everything and
filtering in pandas). Only filters the user actually set become conditions,
and every value is passed as a bound parameter — never string-formatted into
the SQL — so user input can't inject SQL.

>>> w = WhereBuilder()
>>> _ = w.add("t.conference_name = ?", "Eastern").add("t.division_name = ?", None)
>>> w.sql(), w.params
(' WHERE t.conference_name = ?', ['Eastern'])
"""
from __future__ import annotations

from typing import Any, Iterable


class WhereBuilder:
    def __init__(self) -> None:
        self.conditions: list[str] = []
        self.params: list[Any] = []

    @staticmethod
    def _is_set(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return value.strip() != "" and value not in ("All", "Any")
        if isinstance(value, (list, tuple, set)):
            return len(value) > 0
        return True

    def add(self, condition: str, *values: Any) -> "WhereBuilder":
        """Add ``condition`` only if every bound value is set. ``?`` count must match values."""
        if values and not all(self._is_set(v) for v in values):
            return self
        if condition.count("?") != len(values):
            raise ValueError(f"placeholder/value mismatch in: {condition}")
        self.conditions.append(condition)
        self.params.extend(v.strip() if isinstance(v, str) else v for v in values)
        return self

    def add_in(self, column: str, values: Iterable[Any] | None) -> "WhereBuilder":
        values = [v for v in (values or []) if self._is_set(v)]
        if values:
            self.conditions.append(f"{column} IN ({', '.join('?' * len(values))})")
            self.params.extend(values)
        return self

    def add_like(self, expression: str, text: str | None) -> "WhereBuilder":
        """Case-insensitive 'contains' search. LIKE wildcards typed by the user are escaped."""
        if self._is_set(text):
            escaped = text.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            self.conditions.append(f"{expression} LIKE ? ESCAPE '\\'")
            self.params.append(f"%{escaped}%")
        return self

    def sql(self, keyword: str = "WHERE") -> str:
        return f" {keyword} " + " AND ".join(self.conditions) if self.conditions else ""
