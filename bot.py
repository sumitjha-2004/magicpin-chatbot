"""
bot.py — magicpin AI Challenge submission ("Vera, but better").

Implements the 5-endpoint contract from challenge-testing-brief.md, backed
by the deterministic composer in composer.py and the multi-turn handler in
conversation_handlers.py.

Also serves the interactive Vera web chatbot UI from /static.

Run:
    pip install fastapi uvicorn pydantic
    uvicorn bot:app --host 0.0.0.0 --port 8080

Open in browser:
    http://localhost:8080

Dataset auto-load:
    On startup, every file under DATASET_DIR (default: ./dataset next to
    this file) is pushed into the same in-memory context store /v1/context
    writes to — categories/*.json, merchants/*.json, customers/*.json,
    triggers/*.json. This is what makes the demo UI's scenario buttons work
    without a separate warmup step: without it, /v1/tick always returns
    {"actions": []} because no MerchantContext/CategoryContext/TriggerContext
    has ever been pushed. Override the directory with the DATASET_DIR env var.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import composer
import conversation_handlers as ch


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Vera Challenge Bot")
START = time.time()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATASET_DIR = Path(os.environ.get("DATASET_DIR", str(BASE_DIR / "dataset")))

SCOPES = ("category", "merchant", "customer", "trigger")

# (scope, context_id) -> {"version": int, "payload": dict}
contexts: dict[tuple[str, str], dict] = {}

# conversation_id -> conversation_handlers state dict
conversations: dict[str, dict] = {}

# suppression_key -> set of previously sent message bodies
sent_by_suppression_key: dict[str, set[str]] = {}


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------
# StaticFiles(directory=...) raises RuntimeError at import time if the
# directory doesn't exist, which would crash the whole app before any
# endpoint is reachable — guard it so a missing/renamed static/ folder
# degrades to "no UI" instead of "bot won't start at all".

STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def home():
    """Serve the interactive Vera chatbot frontend at http://localhost:8080/."""
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="static/index.html not found")
    return FileResponse(index)


# ---------------------------------------------------------------------------
# Dataset auto-load
# ---------------------------------------------------------------------------

def _store(scope: str, context_id: str, payload: dict, version: int = 1) -> None:
    key = (scope, context_id)
    cur = contexts.get(key)
    if cur and cur["version"] >= version:
        return
    contexts[key] = {"version": version, "payload": payload}


def _load_dataset() -> dict[str, int]:
    """Best-effort: walk DATASET_DIR and push everything found into `contexts`.
    Silently skips anything missing rather than crashing startup — a partial
    dataset (e.g. just categories/) still lets the rest of the bot run."""
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    if not DATASET_DIR.exists():
        return counts

    cat_dir = DATASET_DIR / "categories"
    if cat_dir.exists():
        for f in cat_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                slug = data.get("slug", f.stem)
                _store("category", slug, data)
                counts["category"] += 1
            except (json.JSONDecodeError, OSError):
                continue

    id_field = {"merchant": "merchant_id", "customer": "customer_id", "trigger": "id"}
    for scope, field in id_field.items():
        d = DATASET_DIR / f"{scope}s"
        if not d.exists():
            continue
        for f in d.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                cid = data.get(field)
                if not cid:
                    continue
                _store(scope, cid, data)
                counts[scope] += 1
            except (json.JSONDecodeError, OSError):
                continue

    return counts


@app.on_event("startup")
async def on_startup():
    counts = _load_dataset()
    print(
        f"[startup] loaded from {DATASET_DIR}: "
        f"{counts['category']} categories, {counts['merchant']} merchants, "
        f"{counts['customer']} customers, {counts['trigger']} triggers"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _get(scope: str, context_id: str) -> Optional[dict]:
    entry = contexts.get((scope, context_id))
    return entry["payload"] if entry else None


def _category_for_merchant(merchant: dict) -> dict:
    slug = merchant.get("category_slug", "")
    return _get("category", slug) or {}


# ---------------------------------------------------------------------------
# GET /v1/healthz
# ---------------------------------------------------------------------------

@app.get("/v1/healthz")
async def healthz():
    counts = {s: 0 for s in SCOPES}
    for (scope, _cid) in contexts.keys():
        counts[scope] = counts.get(scope, 0) + 1
    return {"status": "ok", "uptime_seconds": int(time.time() - START), "contexts_loaded": counts}


# ---------------------------------------------------------------------------
# GET /v1/metadata
# ---------------------------------------------------------------------------

@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": "Solo",
        "team_members": ["Candidate"],
        "model": "deterministic-template-composer (no external LLM call required)",
        "approach": (
            "Rule-based composer dispatched by TriggerContext.kind (26 dedicated "
            "handlers + 2 generic fallbacks). Every fact in a message is read "
            "directly from the pushed context dicts — nothing invented — which "
            "makes output deterministic, instant, and immune to hallucination "
            "penalties by construction. An llm_enhance() hook in composer.py is "
            "a documented no-op that a live LLM call can replace for tone-"
            "polishing without touching fact-selection logic."
        ),
        "contact_email": "candidate@example.com",
        "version": "1.0.0",
        "submitted_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# POST /v1/context
# ---------------------------------------------------------------------------

class CtxBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: Optional[str] = None


@app.post("/v1/context")
async def push_context(body: CtxBody):
    if body.scope not in SCOPES:
        return {"accepted": False, "reason": "invalid_scope", "details": f"scope must be one of {SCOPES}"}
    key = (body.scope, body.context_id)
    cur = contexts.get(key)
    if cur and cur["version"] >= body.version:
        return {"accepted": False, "reason": "stale_version", "current_version": cur["version"]}
    contexts[key] = {"version": body.version, "payload": body.payload}
    return {"accepted": True, "ack_id": f"ack_{body.context_id}_v{body.version}", "stored_at": _now_iso()}


# ---------------------------------------------------------------------------
# POST /v1/tick
# ---------------------------------------------------------------------------

class TickBody(BaseModel):
    now: str
    available_triggers: list[str] = []


def _dedup_ok(suppression_key: str, body_text: str) -> bool:
    seen = sent_by_suppression_key.setdefault(suppression_key, set())
    if body_text in seen:
        return False
    seen.add(body_text)
    return True


@app.post("/v1/tick")
async def tick(body: TickBody):
    actions = []
    seen_merchant_conv_this_tick: set[tuple[str, str]] = set()

    for trg_id in body.available_triggers[:20]:  # respect the 20-actions/tick cap
        trigger = _get("trigger", trg_id)
        if not trigger:
            continue

        merchant_id = trigger.get("merchant_id")
        merchant = _get("merchant", merchant_id) if merchant_id else None
        if not merchant:
            continue

        category = _category_for_merchant(merchant)
        if not category:
            continue

        customer_id = trigger.get("customer_id")
        customer = _get("customer", customer_id) if customer_id else None
        if trigger.get("scope") == "customer" and not customer:
            continue  # never fabricate a missing customer

        composed = composer.compose(category, merchant, trigger, customer)
        suppression_key = composed.get("suppression_key") or trigger.get("suppression_key", trg_id)

        conv_key = (merchant_id, trg_id)
        if conv_key in seen_merchant_conv_this_tick:
            continue
        seen_merchant_conv_this_tick.add(conv_key)

        conversation_id = f"conv_{merchant_id}_{trg_id}"[:80]
        existing_state = conversations.get(conversation_id)

        if existing_state is not None:
            # Same trigger fired again for a conversation we've already
            # started (e.g. the demo UI's scenario button clicked a second
            # time). Resume it with its original opening message instead of
            # applying the anti-repetition dedup below — that dedup exists
            # to stop *new* nudges from repeating a body across ticks, not
            # to block reconnecting to a conversation that's still open.
            if existing_state.get("ended"):
                continue
            opening_body = (existing_state.get("sent_bodies") or [composed["body"]])[0]
            actions.append({
                "conversation_id": conversation_id,
                "merchant_id": merchant_id,
                "customer_id": customer_id,
                "send_as": composed["send_as"],
                "trigger_id": trg_id,
                "template_name": f"vera_{trigger.get('kind','generic')}_v1",
                "template_params": [opening_body],
                "body": opening_body,
                "cta": existing_state.get("last_cta") or composed["cta"],
                "suppression_key": suppression_key,
                "rationale": "Resumed an already-open conversation for this (merchant, trigger) pair rather than re-composing.",
            })
            continue

        # Genuinely new conversation for this trigger: anti-repetition dedup
        # applies here — never send the same body twice under one
        # suppression key across separate conversations/ticks.
        if not _dedup_ok(suppression_key, composed["body"]):
            continue

        conversations[conversation_id] = ch.new_state(
            conversation_id,
            merchant_id,
            customer_id,
            composed["send_as"],
            composed["body"],
            trigger_kind=trigger.get("kind"),
            cta=composed.get("cta"),
        )

        actions.append({
            "conversation_id": conversation_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": composed["send_as"],
            "trigger_id": trg_id,
            "template_name": f"vera_{trigger.get('kind','generic')}_v1",
            "template_params": [composed["body"]],
            "body": composed["body"],
            "cta": composed["cta"],
            "suppression_key": suppression_key,
            "rationale": composed["rationale"],
        })

    return {"actions": actions}


# ---------------------------------------------------------------------------
# POST /v1/reply
# ---------------------------------------------------------------------------

class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: Optional[str] = None
    turn_number: int = 0


@app.post("/v1/reply")
async def reply(body: ReplyBody):
    state = conversations.get(body.conversation_id)
    if state is None:
        raise HTTPException(status_code=404, detail="conversation_not_found")

    if state.get("ended"):
        return {"action": "end", "rationale": "Conversation already closed."}

    out = ch.respond(state, body.message, from_role=body.from_role)
    if out.get("action") == "send" and out.get("body"):
        state.setdefault("sent_bodies", []).append(out["body"])
    return out


# ---------------------------------------------------------------------------
# POST /v1/teardown
# ---------------------------------------------------------------------------

@app.post("/v1/teardown")
async def teardown():
    contexts.clear()
    conversations.clear()
    sent_by_suppression_key.clear()
    return {"accepted": True}
