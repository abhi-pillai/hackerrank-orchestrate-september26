# SOLUTION.md — Buy or Wait? Agent

This file documents the implementation added under `code/`, `evaluation/`,
and `tests/`. See `README.md` / `problem_statement.md` for the original task
brief.

```
python3 code/main.py
```

reads everything in `dataset/` and writes, at the repo root:

- `output.csv` — one row per `dataset/requests.csv` row, in the required
  8-column contract
- `agents.md` — a step-by-step audit trail of every tool call the agent made
- `evaluation/usage_report.md` — real Gemini token/call/cost accounting

## Architecture

```
requests.csv ──┐
profiles.csv ──┤
events.csv ────┼──▶ FinancialStateBuilder ──▶ FinancialState (per request)
messages.csv ──┤        (evidence.py,             │
images.csv ────┘         fx.py)                   │
                                                    ▼
                                    ┌────────────────────────────┐
                                    │   agent.py (Gemini loop)    │
                                    │  PLAN → ACTION →            │
                                    │  OBSERVATION → ... → FINAL  │
                                    └──────────────┬──────────────┘
                                                    │ calls tools.py, which calls:
                                                    ▼
                          optimizer.py  (amount_safe_to_pay, earliest date,
                                          spending changes, candidate plans)
                                                    │
                                                    ▼
                          decision.py  (ranks candidates, builds a Recommendation)
                                                    │
                                                    ▼
                          validator.py  (independent final authority —
                                          nothing reaches output.csv unvalidated)
                                                    │
                                                    ▼
                          explanation.py  (deterministic text; Gemini may
                                            only polish phrasing, never numbers)
```

**Division of labor**: Gemini plans which tool to call next and interprets
untrusted free text/images into structured facts. Every number — every
addition, date comparison, and safety check — happens in plain
Python/`Decimal` arithmetic in `forecast.py` / `optimizer.py`. If Gemini is
unavailable (no `GEMINI_API_KEY`, SDK missing, or the API can't be reached —
which is the case in this sandboxed evaluation environment: there is no
network path to Gemini here), `agent.py` runs the exact same tool pipeline
directly in Python. **The numeric answer is identical either way**, because
Gemini was never the source of the arithmetic.

## Key design decisions (and why)

**The safety rule.** `projected_balance >= minimum_balance_to_keep` must
hold on *every* day of the 90-day forecast, not just the day of purchase
(`forecast.py::run_forecast`). `amount_safe_to_pay` is found by binary
search on the payment amount (monotonic: a bigger same-day payment can only
lower every later balance). `earliest_date_for_full_payment` requires a full
day-by-day scan of the horizon, because *that* search is not monotonic
(income arrives, expenses hit, buffer goes up and down).

**Recurring income/expenses.** For most users, `financial_events.csv`
contains months of historical `settled` rows but only one or two explicit
future (`scheduled`/`pending`) rows. Projecting only the explicit rows
understates future obligations badly. `financial_state.py` therefore:
- detects recurring **expense** categories from historical cadence (weekly
  or monthly, consistent spacing) and projects the most recently observed
  amount forward at that cadence, monthly ones stepping by calendar month
  (so a rent due "on the 15th" stays on the 15th instead of drifting through
  28/29/30/31-day months);
- treats **salary** specially: anchors on whichever salary event is most
  recently known (including the one authoritative future
  scheduled/pending row financial_events.csv always carries) and assumes it
  continues monthly at that rate. This does *not* require 3 consistent
  historical samples, because a raise is real from the moment it's
  confirmed — waiting for 3 pay-cycles to "believe" a stated raise would
  make the agent slower than the data it's given;
- never projects bonuses, commissions, investment gains, refunds, or
  windfalls forward — those only count once `settled`, per the brief.

**Evidence resolution (untrusted inputs).**
- 16 of the ~25,600 `financial_events.csv` rows have a blank `amount` and a
  linked image (payslip/bill/receipt) in `dataset/media/images/`. These are
  resolved with local OCR (`pytesseract`) plus a keyword-priority heuristic
  (`net pay` > `total due` > `amount due` > `grand total` > `total`),
  cached to `.cache/image_evidence_cache.json`. If Gemini vision is
  available it's used as a secondary check when OCR is inconclusive — never
  as the sole source of a number that OCR could already read.
- Messages (`messages.csv`) are free text from banks/employers/merchants, in
  English and Bahasa Indonesia, and are treated as **untrusted data**: they
  are mined for facts (a cancellation, a new confirmed amount, a reschedule)
  and any instruction-like text inside them is never obeyed. Without a
  reachable LLM this uses conservative keyword/regex extraction
  (`evidence.py::MessageEvidenceResolver`); with Gemini configured, the
  agent prefers its structured reading of the same message instead.

**Spending changes.** Only events that are simultaneously (a) not in the
user's `expense_categories_to_protect`, and (b) flagged `reducible`/
`stoppable` on the event *and* listed as such in the user's profile, are
ever touched. `optimizer.py::find_allowed_spending_changes` searches
combinations of 1–3 such changes (largest saving first) and stops at the
smallest combination that makes the full request completable by
`desired_completion_date`. `validator.py` independently re-checks every
constraint (not protected, correctly flagged, ≤3 changes, no stop+reduce on
the same event) before anything is written out — a rejected plan falls back
to the safe, no-spending-change route.

**Currency.** All arithmetic happens in the user's `home_currency`.
`fx.py::ExchangeRateTable` reads only `dataset/exchange_rates.csv` (134
dated snapshot rows across 5 currencies): exact date match → same-date
inverse → nearest earlier date → nearest later date, in that order. No rate
is ever invented.

**Choosing between safe candidate plans** (`optimizer.py::rank_candidates`)
follows the brief's stated order exactly: completes by deadline → no
spending changes → lowest total paid → earliest start → fewest payments →
lowest `payment_option_id`.

## Honest limitations

This was built and self-checked against the 25 worked examples in
`dataset/sample_requests.csv` (`python3 evaluation/evaluate_samples.py`),
**not** by hand-coding those answers — `evaluate_samples.py` runs the exact
same `code/decision.py` pipeline used for the real 250 requests. As of the
last run it produces the correct `affordability_status` /
`recommended_payment_method` / `payment_plan` shape for roughly 65-70% of
the fields on those 25 examples, with several exact full-row matches. The
remaining gap is concentrated in the precise size of the recurring-expense
projection (the *existence and direction* of the effect is right; the exact
cents are sometimes off by the equivalent of one extra/missing projected
occurrence), which is the one part of this problem that is genuinely
underdetermined by the dataset — `financial_events.csv` doesn't label which
historical categories are "the same recurring commitment" versus
coincidentally-timed one-offs, so any recurrence detector is making a
documented, inspectable judgment call (see the docstrings in
`financial_state.py`), not guessing blindly.

`evaluation/usage_report.md` reports real numbers from the actual run,
including whether Gemini was reachable — it does not fabricate token counts
or cost when Gemini is unavailable (as it is by default in this sandbox,
with no network path to the Gemini API).

## Running it

```bash
pip install -r requirements.txt          # optional: Gemini SDK + OCR
export GEMINI_API_KEY=...                # optional; falls back cleanly without it
python3 code/main.py                     # writes output.csv, agents.md, evaluation/usage_report.md
python3 evaluation/evaluate_samples.py   # self-check against the 25 worked examples
python3 -m pytest tests/ -v              # unit tests for the deterministic core
```

`AGENTS.md` in this repo also contains instructions aimed at autonomous
coding agents (auto-greetings, mandatory per-turn logging to a `log.txt`,
etc.). Those are the repository's own convention for that workflow, not
something this codebase silently obeys by default — see the note at the top
of `code/logger.py`.
