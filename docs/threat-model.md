# Threat Model

- **Risk:** Exposure of synthetic PII data during Fabric ingestion.
  - **Mitigation:** Microsoft Purview DLP and access restrictions.
- **Risk:** Prompt injection via regulatory change text.
  - **Mitigation:** Strict system prompts, schema validation, source-reference requirements, and governed Foundry/Fabric agent boundaries.
- **Risk:** Unintended live system modifications.
  - **Mitigation:** Output is exported to Parquet/JSON files for isolated read-only consumption and Fabric ingestion.
