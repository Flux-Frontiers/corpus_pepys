# Diary Transformer — Run Summary

| Field | Value |
|---|---|
| Date | 2026-08-16 |
| Time | 23:36:15 |
| Version | 0.97.0 |

## Invocation

```
/Users/egs/repos/corpus_pepys/.venv/bin/diary-transformer transform data/pepys_clean.txt data/pepys_enriched_full.txt --topics-file config/pepys_only_topics.yaml --restart --batch-size 0
```

## Inputs & Outputs

| Parameter | Value |
|---|---|
| Input file | `data/pepys_clean.txt` |
| Output file | `data/pepys_enriched_full.txt` |

## Run Parameters

| Parameter | Value |
|---|---|
| Batch Size | `0` |
| Chunk Size | `512` |
| Max Chunks Per Entry | `3` |
| Chunking Strategy | `sentence_group` |
| Seed | `None` |

## Pipeline Statistics

| Metric | Value |
|---|---|
| Entries parsed | 3355 |
| Entries selected | 3355 |
| Entries generated | 7282 |
| Time range | 1660-01-01 → 1669-08-02 |
| Runtime | 251.9s |
