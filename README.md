# Vera Challenge Submission

## Approach

The composer is **deterministic and template-based, not an LLM call**. `composer.py`
dispatches on `TriggerContext.kind` to one of 26 dedicated handler functions
(plus two generic fallbacks for any kind — or thin/placeholder payload — it
hasn't special-cased). Every handler builds the message body by reading
fields straight out of the four context dicts: owner name, real performance
deltas, real peer-stat comparisons, real digest items with their source
citations, real offer-catalog entries, real customer relationship/state.
Nothing is invented, so the "hallucinated data" anti-pattern is structurally
impossible rather than something a prompt has to be told not to do, and the
"same inputs → same output" determinism the brief requires (§7.1) is free.

`conversation_handlers.py` implements the optional multi-turn contract
(§7.4) and targets the three replay scenarios directly:
- **Auto-reply detection** — a canned-phrase wordlist plus "identical to the
  previous inbound" check. First occurrence → one gentle nudge. Second in a
  row → `wait` 24h. Third → `end`. This is the exact "burns 2-3 turns"
  failure mode called out in the brief's pain-points list (§3.1).
- **Intent-transition handoff** — an explicit-commitment wordlist ("let's do
  it", "go ahead", "kar do", …) short-circuits straight to an `action`-mode
  reply instead of falling through to another qualifying question — the
  Pattern-D anti-example (§9) the brief penalizes.
- **Hostile / not-interested / off-topic** — hostility gets one short
  apology-and-opt-out send, then the conversation ends; explicit
  disinterest ends immediately without a re-pitch; off-topic asks (GST,
  loans, etc.) get a polite one-line redirect that stays on-mission.

`bot.py` is the thin FastAPI shell around both modules, implementing the
5-endpoint contract from `challenge-testing-brief.md` verbatim: idempotent
versioned `/v1/context`, `/v1/tick` with a per-tick suppression-key dedup
cache (never sends the same body twice under the same key — the anti-
repetition rule) and a one-action-per-`(merchant, conversation)`-per-tick
cap, `/v1/reply` backed by `conversation_handlers.respond()`, plus
`/v1/healthz`, `/v1/metadata`, and an optional `/v1/teardown`.

## Why not call an LLM?

Three reasons, in order of importance:
1. **Zero fabrication risk.** A template that only ever interpolates real
   context fields cannot hallucinate a citation or a competitor name — the
   single costliest anti-pattern in the rubric. An LLM prompt has to be
   *told* not to fabricate; a template structurally can't.
2. **Determinism and latency for free.** The brief requires temperature-0
   reproducibility and a 30s budget per call. Templates are instant and
   perfectly reproducible without needing to pin a model version or worry
   about provider drift.
3. **It's still swappable.** `composer.py` exposes an `llm_enhance(composed,
   category, merchant, trigger, customer)` hook, currently a documented
   no-op, that a real submission can wire up to an LLM call for a
   tone-polish pass over `composed['body']` — rewording without touching
   which facts got selected. That keeps the fact-selection logic (the part
   most exposed to hallucination) template-driven while still allowing a
   more natural final voice if compute budget allows.

## What additional context would have helped most

- **A real slot-availability / booking-calendar field** on `MerchantContext`
  — several triggers (recall, appointment, trial-followup) only had slots
  in the seed data, not the generated 75%, which is why those messages fall
  back to an open-ended "tell us a time that works" CTA instead of a
  multi-choice slot offer.
- **A explicit "language preference" field on `MerchantContext.identity`**
  distinct from the broader `languages` list — right now the bot infers
  Hindi-English mix from `"hi" in languages`, which is a reasonable proxy
  but a dedicated field (mirroring `CustomerContext.identity.language_pref`)
  would remove the guesswork.
- **Non-placeholder payloads on all generated triggers.** ~40% of the 100
  triggers in the expanded dataset carry `{"placeholder": true}` instead of
  kind-specific data (this is a property of `generate_dataset.py`'s
  `expand_triggers`, not something the bot can fix) — for those, the
  composer correctly refuses to invent detail and falls back to whatever
  real signal it can find on the merchant/customer, which is safe but
  visibly thinner than the seed-backed messages.

## Files

| File | Purpose |
|---|---|
| `composer.py` | The 4-context → message composition logic (no dependencies). |
| `conversation_handlers.py` | Multi-turn `respond()` state machine. |
| `bot.py` | FastAPI server wiring both into the 5-endpoint contract. |
| `submission.jsonl` | Output of `build_submission.py` over the 30 canonical test pairs. |
| `build_submission.py` | Dev script: runs `composer.compose()` over `dataset/test_pairs.json` and writes `submission.jsonl`. Not part of the runtime bot. |

Run locally:
```bash
pip install fastapi uvicorn pydantic
uvicorn bot:app --host 0.0.0.0 --port 8080
python judge_simulator.py   # against BOT_URL=http://localhost:8080
```
