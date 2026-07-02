# Threat Model

- **Risk:** Exposure of synthetic PII data during Fabric ingestion.
  - **Mitigation:** Microsoft Purview DLP and access restrictions.
- **Risk:** Prompt injection via regulatory change text.
  - **Mitigation:** Strict system prompts for the Interpretation agent and fallback deterministic checks.
- **Risk:** Unintended live system modifications.
  - **Mitigation:** Output is exclusively exported to Parquet/JSON files for isolated offline/read-only consumption.
