# Model deprecations

Anthropic retires models on a published schedule.

## Model status

| API model name               | Current state | Deprecated         | Tentative retirement date         |
| :--------------------------- | :------------ | :----------------- | :-------------------------------- |
| `claude-active-model-20260101` | Active        | N/A                | N/A                               |
| claude-future-model-20260401 | Active        | N/A                | Not sooner than June 1, 2027      |
| claude-preview-model-20260101 | Deprecated    | June 9, 2026       | To be announced                   |
| `claude-old-sonnet-20250101` | Deprecated    | September 1, 2026  | March 1, 2027                     |
| `claude-old-haiku-20240101`  | Retired       | May 1, 2026        | August 1, 2026                    |

## Deprecation history

### 2026-09-01: Old Sonnet

| Retirement Date | Deprecated Model | Recommended Replacement |
|---|---|---|
| March 1, 2027 | [`claude-old-sonnet-20250101`](https://docs.example.test/models) | `claude-new-sonnet-20260601` |
