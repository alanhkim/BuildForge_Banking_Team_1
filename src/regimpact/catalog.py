"""Loads the reference catalog and exposes interpreter-compatible fixtures."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .contracts import Obligation

CATALOG_PATH = Path(__file__).with_name("catalog.yaml")


@lru_cache(maxsize=1)
def load_catalog(path: str | Path | None = None) -> dict[str, Any]:
    """Read and cache the reference catalog."""
    catalog_path = Path(path) if path else CATALOG_PATH
    with catalog_path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class CatalogFixture:
    """Deterministic catalog adapter for the Regulation Interpreter contract."""

    def __init__(self, path: str | Path | None = None):
        self._catalog = load_catalog(path)
        self._regulations = {
            regulation["id"]: regulation
            for regulation in self._catalog.get("regulations", [])
        }

    def get_obligations(
        self,
        regulation_id: str,
        change_id: str,
    ) -> list[Obligation] | None:
        """Return typed interpreter obligations for a regulation change."""
        regulation = self._regulations.get(regulation_id)
        if regulation is None:
            return None

        expected_change_id = f"CHG-{regulation['short_code']}"
        if change_id != expected_change_id:
            return None

        themes = self._catalog.get("themes", {})
        obligations: list[Obligation] = []
        for index, template in enumerate(
            regulation.get("obligation_templates", []),
            start=1,
        ):
            obligation_id = f"OBL-{regulation['short_code']}-{index:02d}"
            theme = template["theme"]
            theme_spec = themes.get(theme, {})
            obligations.append(
                Obligation(
                    id=obligation_id,
                    change_id=change_id,
                    theme=theme,
                    summary=template["statement"],
                    target_maturity=int(template["target_maturity"]),
                    criticality=str(template["criticality"]),
                    affected_data_domain_ids=list(theme_spec.get("data_domains", [])),
                    source_refs=[f"catalog:{regulation_id}:{obligation_id}"],
                    notes=[f"source article: {template.get('article', 'n/a')}"],
                )
            )

        return obligations or None

    def has_entry(self, regulation_id: str, change_id: str) -> bool:
        """Check if catalog has obligations for the given regulation change."""
        return self.get_obligations(regulation_id, change_id) is not None

    def list_regulations(self) -> list[str]:
        """List all regulation IDs in the catalog."""
        return list(self._regulations.keys())
