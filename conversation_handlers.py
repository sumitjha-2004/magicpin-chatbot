"""
conversation_handlers.py — optional multi-turn capability (challenge brief §7.4).

respond(state, message, from_role="merchant") -> dict with keys: action,
body?, cta?, wait_seconds?, rationale.

Two distinct personas can be on the other end of a conversation, and they
need different handling:

  - from_role="merchant": Vera is talking to the business owner. Covers the
    three "open challenges" the brief flags explicitly —
      1. Auto-reply detection (same canned text repeated) -> nudge once,
         then back off, then end. (challenge-brief.md §12.1)
      2. Intent-transition handoff — explicit commitment words switch the
         bot from qualifying to action immediately. (§12.2, and the
         Pattern-D anti-example in §9)
      3. Graceful exit on hostility / "not interested" / repeated silence.
         (§12.5)

  - from_role="customer": Vera is talking *as* the merchant, to one of the
    merchant's customers (e.g. confirming an appointment, or re-engaging a
    lapsed customer). This needs its own, much simpler confirm/decline
    handling — a customer saying "yes" to an appointment reminder should
    never fall into the merchant-side "drafting a GBP post" action-mode
    reply.

ConversationState is a plain dict so it can be stored however bot.py likes
(in-memory dict keyed by conversation_id is what bot.py does).
"""

from __future__ import annotations

import re
from typing import Any, Optional

Ctx = dict[str, Any]

# ---------------------------------------------------------------------------
# merchant-facing marker banks
# ---------------------------------------------------------------------------

AUTO_REPLY_MARKERS = (
    "thank you for contacting", "thanks for contacting", "will respond shortly",
    "team will get back", "automated assistant", "out of office", "we will revert",
    "aapki jaankari ke liye", "hamari team tak pahuncha", "shukriya", "team se baat",
)

INTENT_COMMIT_MARKERS = (
    "let's do it", "lets do it", "ok let's", "go ahead", "yes let's", "sounds good let's",
    "start it", "sign me up", "i want to join", "join karna hai", "haan chalo",
    "proceed", "yes please do", "kar do", "kar dijiye", "chalo shuru",
)

HOSTILE_MARKERS = (
    "stop messaging", "stop sending", "useless", "spam", "leave me alone", "harass",
    "don't message", "dont message", "waste of time", "bakwas", "bandh karo",
    "unsubscribe", "remove me",
)

NOT_INTERESTED_MARKERS = (
    "not interested", "no thanks", "not now", "nahi chahiye", "abhi nahi",
)

OFF_TOPIC_MARKERS = (
    "gst", "income tax", "loan", "visa", "insurance claim", "personal problem",
)

# ---------------------------------------------------------------------------
# customer-facing marker banks (word-boundary matched — "yes"/"no" are too
# short to safely substring-match, e.g. "no" inside "know"/"not"/"now")
# ---------------------------------------------------------------------------

CUSTOMER_CONFIRM_WORDS = (
    "yes", "confirm", "confirmed", "sure", "ok", "okay", "haan", "book", "booking",
    "sounds good", "works for me", "done",
)

CUSTOMER_DECLINE_WORDS = (
    "no", "cancel", "nahi", "not interested", "not now",
)

CUSTOMER_RESCHEDULE_WORDS = (
    "reschedule", "different time", "another time", "change time", "postpone",
)


def new_state(
    conversation_id: str,
    merchant_id: str,
    customer_id: Optional[str],
    send_as: str,
    last_body: str = "",
    trigger_kind: Optional[str] = None,
    cta: Optional[str] = None,
) -> Ctx:
    return {
        "conversation_id": conversation_id,
        "merchant_id": merchant_id,
        "customer_id": customer_id,
        "send_as": send_as,
        "trigger_kind": trigger_kind,
        "last_cta": cta,
        "turns": 0,
        "sent_bodies": [last_body] if last_body else [],
        "last_inbound": None,
        "consecutive_same_inbound": 0,
        "consecutive_auto_reply_nudges": 0,
        "ended": False,
        "mode": "pitch",  # "pitch" | "action" | "closing"
    }


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    t = _norm(text)
    return any(m in t for m in markers)


def _contains_word(text: str, words: tuple[str, ...]) -> bool:
    """Word-boundary match — for short tokens like 'yes'/'no' that would
    false-positive as substrings ('no' inside 'know', 'not', 'now')."""
    t = _norm(text)
    for w in words:
        if " " in w:  # multi-word phrase — substring match is fine/safer
            if w in t:
                return True
        elif re.search(rf"\b{re.escape(w)}\b", t):
            return True
    return False


def respond(state: Ctx, message: str, from_role: str = "merchant") -> dict:
    """Given the conversation-so-far state + the latest inbound message,
    decide: send / wait / end. Mutates `state` in place (bot.py persists it)."""
    state["turns"] = state.get("turns", 0) + 1
    previous_inbound = state.get("last_inbound")
    same_as_last = bool(previous_inbound) and _norm(message) == _norm(previous_inbound)
    state["consecutive_same_inbound"] = state.get("consecutive_same_inbound", 0) + 1 if same_as_last else 1
    state["last_inbound"] = message

    # A customer replying (directly, or because Vera is messaging on the
    # merchant's behalf) gets the simpler confirm/decline flow, never the
    # merchant-side auto-reply/intent-transition machinery.
    is_customer = from_role == "customer" or state.get("send_as") == "merchant_on_behalf"
    if is_customer:
        return _respond_customer(state, message)
    return _respond_merchant(state, message)


# ---------------------------------------------------------------------------
# merchant-facing flow
# ---------------------------------------------------------------------------

def _respond_merchant(state: Ctx, merchant_message: str) -> dict:
    # --- 1. Hostility -> graceful, immediate exit -------------------------
    if _contains_any(merchant_message, HOSTILE_MARKERS):
        state["ended"] = True
        return {
            "action": "send",
            "body": "Apologies — I won't message again. If anything changes, just say 'Hi Vera' to restart. 🙏",
            "cta": "none",
            "rationale": "Merchant frustration explicit; one-line acknowledgment + opt-out path, then closing without further engagement.",
        }

    # --- 2. Explicit "not interested" -> exit without a plea ---------------
    if _contains_any(merchant_message, NOT_INTERESTED_MARKERS) and not _contains_any(merchant_message, INTENT_COMMIT_MARKERS):
        state["ended"] = True
        return {
            "action": "end",
            "rationale": "Merchant explicitly signaled disinterest; exiting rather than re-pitching.",
        }

    # --- 3. Auto-reply detection ---------------------------------------------
    # Either a canned-phrase marker, or the same exact text sent twice+ in a
    # row (state["consecutive_same_inbound"] was already updated in respond()
    # before we got here, so it already reflects *this* message).
    if _contains_any(merchant_message, AUTO_REPLY_MARKERS) or state["consecutive_same_inbound"] > 1:
        state["consecutive_auto_reply_nudges"] = state.get("consecutive_auto_reply_nudges", 0) + 1
        n = state["consecutive_auto_reply_nudges"]
        if n == 1:
            return {
                "action": "send",
                "body": "Looks like an auto-reply 😊 When the owner sees this, a quick 'Yes' is all it takes to move forward.",
                "cta": "binary_yes_no",
                "rationale": "First occurrence of a canned/auto-reply pattern; one gentle nudge rather than repeating the pitch.",
            }
        elif n == 2:
            return {
                "action": "wait",
                "wait_seconds": 86400,
                "rationale": "Same auto-reply twice in a row — owner likely not at the phone. Backing off 24h instead of burning more turns.",
            }
        else:
            state["ended"] = True
            return {
                "action": "end",
                "rationale": "Auto-reply 3+ times with zero real engagement signal; closing to avoid wasting turns (per brief §3's auto-reply pain point).",
            }

    # --- 4. Explicit intent commitment -> switch straight to action --------
    if _contains_any(merchant_message, INTENT_COMMIT_MARKERS):
        state["mode"] = "action"
        return {
            "action": "send",
            "body": "Great — starting now. Drafting the first version and I'll share it here in a moment; reply STOP anytime if you want to pause.",
            "cta": "none",
            "rationale": "Merchant gave explicit go-ahead; switching straight to action mode instead of another qualifying question (the exact Pattern-D failure the brief penalizes).",
        }

    # --- 5. Off-topic but not hostile -> brief redirect, stay on mission ----
    if _contains_any(merchant_message, OFF_TOPIC_MARKERS):
        return {
            "action": "send",
            "body": "That's outside what I can help with here, but happy to keep going on your listing/offers whenever you're ready — want to pick back up?",
            "cta": "open_ended",
            "rationale": "Off-topic question; politely declines and redirects to the mission without ignoring the merchant.",
        }

    # --- 6. Default: acknowledge + advance one concrete step ---------------
    if state.get("mode") == "action":
        body = "Done — sent through. Let me know if you'd like any tweaks."
        rationale = "Already in action mode from a prior commitment; reporting completion rather than re-asking."
    else:
        body = "Got it — noted. Want me to go ahead and put that together?"
        rationale = "Neutral engaged reply with no clear signal yet; advancing with a single low-friction next-step question."
    state["sent_bodies"].append(body)
    return {"action": "send", "body": body, "cta": "binary_yes_no", "rationale": rationale}


# ---------------------------------------------------------------------------
# customer-facing flow
# ---------------------------------------------------------------------------

_CONFIRM_COPY = {
    "appointment_tomorrow": "Great, you're confirmed for tomorrow — see you then! 🙌",
    "recall_due": "Perfect, you're booked in — we'll see you then. Reply here if anything changes.",
    "trial_followup": "Awesome, locking that slot in for you. See you soon!",
    "wedding_package_followup": "Wonderful — I'll hold that session for you. Excited to be part of the prep! 💍",
    "chronic_refill_due": "Got it — dispatching your order now. It'll reach the saved address.",
    "customer_lapsed_soft": "Great to have you back! I'll hold a slot — what day works best for you?",
    "customer_lapsed_hard": "Great to have you back! I'll hold a slot — what day works best for you?",
}

_DECLINE_COPY = {
    "appointment_tomorrow": "No problem — want to pick a different time instead?",
    "recall_due": "No worries. Just say the word whenever you're ready to rebook.",
    "trial_followup": "All good — reach out whenever a session works for you.",
    "wedding_package_followup": "Understood — no rush, we'll be here when the time's right.",
    "chronic_refill_due": "Okay, skipping this refill. Message us whenever you need it dispatched.",
    "customer_lapsed_soft": "No worries at all — the offer stays open whenever you're ready.",
    "customer_lapsed_hard": "No worries at all — the offer stays open whenever you're ready.",
}


def _respond_customer(state: Ctx, message: str) -> dict:
    kind = state.get("trigger_kind") or ""

    # --- 1. Hostility / opt-out -> immediate, graceful exit -----------------
    if _contains_any(message, HOSTILE_MARKERS):
        state["ended"] = True
        return {
            "action": "send",
            "body": "Understood, you won't hear from us again unless you reach out first. Take care! 🙏",
            "cta": "none",
            "rationale": "Customer asked to stop / unsubscribe; single acknowledgment then close, no further messaging.",
        }

    # --- 2. Reschedule (appointment-specific nuance before generic decline) -
    if kind == "appointment_tomorrow" and _contains_word(message, CUSTOMER_RESCHEDULE_WORDS):
        return {
            "action": "send",
            "body": "Sure — what day/time works better for you? I'll get it moved.",
            "cta": "open_ended",
            "rationale": "Customer wants to reschedule rather than cancel outright; asking for their preferred slot instead of just closing.",
        }

    # --- 3. Confirm ------------------------------------------------------------
    if _contains_word(message, CUSTOMER_CONFIRM_WORDS):
        body = _CONFIRM_COPY.get(kind, "Great, thanks for confirming!")
        ends_here = kind not in ("customer_lapsed_soft", "customer_lapsed_hard")  # those need a follow-up slot
        state["ended"] = ends_here
        return {
            "action": "send",
            "body": body,
            "cta": "none" if ends_here else "open_ended",
            "rationale": f"Customer confirmed on a '{kind or 'generic'}' conversation; sent the matching confirmation copy rather than a generic reply.",
        }

    # --- 4. Decline ------------------------------------------------------------
    if _contains_word(message, CUSTOMER_DECLINE_WORDS):
        body = _DECLINE_COPY.get(kind, "No worries — reach out whenever works for you.")
        state["ended"] = True
        return {
            "action": "send",
            "body": body,
            "cta": "none",
            "rationale": f"Customer declined on a '{kind or 'generic'}' conversation; polite no-pressure close instead of re-pitching.",
        }

    # --- 5. Anything else -> acknowledge, keep it simple ------------------------
    return {
        "action": "send",
        "body": "Got it, thanks for letting us know! Reply YES to confirm or let us know if you'd like a different time.",
        "cta": "binary_yes_no",
        "rationale": "Customer message didn't match confirm/decline/hostile patterns; falling back to a single clear yes/no prompt rather than guessing intent.",
    }
