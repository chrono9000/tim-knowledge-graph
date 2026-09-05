# Processed data

The ingestion agent writes `ingestion-manifest.json` here after a successful non-dry run. It records each raw filename, latest content hash, immutable source ID, source timestamp, and processing timestamp so unchanged files can be skipped safely.

Future normalized, human-reviewable intermediate records may also live here. Nothing in this directory runs automatically.
