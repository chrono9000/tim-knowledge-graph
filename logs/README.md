# Logs

Each non-dry ingestion run writes one `ingestion-<timestamp>.jsonl` audit file here. Events record source additions, node and edge additions or merges, processed/skipped/error files, and the final run summary. Dry runs never write logs.
