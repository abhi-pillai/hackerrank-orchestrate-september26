# Token Usage and Cost Report

Generated: 2026-09-13T11:08:12.433447+00:00

This report reflects the run that produced `output.csv` in this repository.

## Summary

- Requests processed: 250
- Requests where the Gemini agent loop produced the accepted recommendation: 100
- Requests handled by the deterministic fallback (no LLM / validation rejected the LLM path): 150
- Requests that raised an unhandled error: 0
- Total run duration: 1895.29s

## Model

- Provider: google-gemini
- Model: gemini-3.1-flash-lite
- Gemini SDK available and reachable this run: True

## Calls

- Model calls: 501
- Vision calls: 0
- Retries: 497
- Failures (after retries): 156

## Tokens

- Input tokens: 174647
- Output tokens: 21793
- Total tokens: 196440
- Average tokens per model call: 392.10

If the Gemini SDK was not installed/configured, or the API could not be reached from this environment, `calls` is 0 and every request was solved by the deterministic financial engine described in the README. No token or cost figures are estimated or fabricated in that case.

## Estimated cost

- Estimated total cost: 0.0001 (based on GEMINI_INPUT_PRICE_PER_1K/GEMINI_OUTPUT_PRICE_PER_1K)
- Estimated cost per request: 0.000000
