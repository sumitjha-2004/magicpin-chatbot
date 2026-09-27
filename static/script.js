let conversationId = null;
let isSending = false;
let currentScenario = null;


/* =========================================================
   TRIGGER IDs
   ========================================================= */

const SCENARIOS = {

    appointment:
        "trg_076_appointment_tomorrow_m_019_karim_salon_lu",

    customer:
        "trg_071_customer_lapsed_soft_m_014_dr_asha_dentis",

    performance:
        "trg_004_perf_dip_bharat"

};


/* =========================================================
   DOM ELEMENTS
   ========================================================= */

const messages = document.getElementById("chatMessages");
const input = document.getElementById("messageInput");
const sendButton = document.getElementById("sendButton");
const typingIndicator = document.getElementById("typingIndicator");
const quickReplies = document.getElementById("quickReplies");


/* =========================================================
   ADD MESSAGE
   ========================================================= */

function addMessage(text, role = "vera") {

    const row = document.createElement("div");

    row.className =
        `message-row ${role === "customer" ? "user" : ""}`;

    const avatar = document.createElement("div");

    avatar.className = "message-avatar";

    avatar.textContent =
        role === "customer" ? "You" : "V";

    const bubble = document.createElement("div");

    bubble.className = "message-bubble";

    bubble.textContent = text;

    row.appendChild(avatar);
    row.appendChild(bubble);

    messages.appendChild(row);

    messages.scrollTop = messages.scrollHeight;
}


/* =========================================================
   TYPING INDICATOR
   ========================================================= */

function showTyping() {

    typingIndicator.classList.remove("hidden");

    messages.scrollTop = messages.scrollHeight;
}


function hideTyping() {

    typingIndicator.classList.add("hidden");
}


/* =========================================================
   START SCENARIO
   ========================================================= */

async function selectScenario(scenario) {

    const triggerId = SCENARIOS[scenario];

    if (!triggerId) {

        addMessage(
            "Sorry, this scenario is not available.",
            "vera"
        );

        return;
    }


    currentScenario = scenario;


    /*
     * Start a completely new conversation.
     */

    conversationId = null;

    clearQuickReplies();


    /*
     * Clear welcome screen.
     */

    messages.innerHTML = "";


    /*
     * Show loading state.
     */

    showTyping();


    try {

        /*
         * Ask backend to process this trigger.
         */

        const response = await fetch("/v1/tick", {

            method: "POST",

            headers: {
                "Content-Type": "application/json"
            },

            body: JSON.stringify({

                now: new Date().toISOString(),

                available_triggers: [
                    triggerId
                ]

            })

        });


        const data = await response.json();


        if (!response.ok) {

            throw new Error(
                data.detail || "Unable to start conversation."
            );

        }


        hideTyping();


        /*
         * No action means the required context has not
         * been loaded into the backend.
         */

        if (
            !data.actions ||
            data.actions.length === 0
        ) {

            addMessage(
                "I couldn't start this conversation because the required business context is not loaded.",
                "vera"
            );

            return;
        }


        /*
         * Take the first action returned by /v1/tick.
         */

        const action = data.actions[0];


        /*
         * IMPORTANT:
         * Store the REAL conversation ID.
         */

        conversationId = action.conversation_id;


        /*
         * Display Vera's initial message.
         */

        if (action.body) {

            addMessage(
                action.body,
                "vera"
            );

        }


        /*
         * Show CTA buttons if available.
         */

        handleCTA(action.cta);


    } catch (error) {

        hideTyping();

        console.error(
            "Scenario start error:",
            error
        );

        addMessage(
            "Sorry, I couldn't start the conversation. Please try again.",
            "vera"
        );

    }

}


/* =========================================================
   SEND MESSAGE
   ========================================================= */

async function sendMessage(customMessage = null) {

    if (isSending) {
        return;
    }


    const message =
        customMessage || input.value.trim();


    if (!message) {
        return;
    }


    /*
     * A conversation must exist.
     */

    if (!conversationId) {

        addMessage(
            "Please select a scenario first.",
            "vera"
        );

        return;
    }


    isSending = true;

    input.value = "";

    clearQuickReplies();

    addMessage(
        message,
        "customer"
    );

    showTyping();


    try {

        const response = await fetch(
            "/v1/reply",
            {

                method: "POST",

                headers: {
                    "Content-Type": "application/json"
                },

                // The chat widget stands in for whichever role the current
                // scenario is talking to. "performance" is a merchant-facing
                // trigger (Vera <-> the business owner); "appointment" and
                // "customer" are customer-facing (Vera messaging on the
                // merchant's behalf to one of their customers). Sending the
                // wrong role here would route replies through the wrong
                // half of conversation_handlers.py's logic (e.g. a customer
                // typing "yes" being treated as merchant auto-reply text).
                body: JSON.stringify({

                    conversation_id:
                        conversationId,

                    from_role:
                        currentFromRole(),

                    message:
                        message

                })

            }
        );


        if (response.status === 404) {

            hideTyping();

            addMessage(
                "This conversation isn't active anymore. Select a scenario to start a new one.",
                "vera"
            );

            conversationId = null;

            return;
        }


        const data = await response.json();


        if (!response.ok) {

            throw new Error(
                data.detail ||
                "Unable to send message."
            );

        }


        hideTyping();


        /*
         * Display Vera's response.
         */

        if (data.body) {

            addMessage(
                data.body,
                "vera"
            );

        }


        /*
         * Handle CTA.
         */

        handleCTA(
            data.cta
        );


        /*
         * Vera is deliberately pausing (e.g. backed off after a repeated
         * auto-reply) rather than ending the conversation outright.
         */

        if (data.action === "wait") {

            addMessage(
                "Vera will follow up later instead of replying right away.",
                "vera"
            );

        }


        /*
         * Conversation ended.
         */

        if (data.action === "end") {

            conversationId = null;

            addMessage(
                "This conversation has ended. Select a scenario to start a new conversation.",
                "vera"
            );

        }


    } catch (error) {

        hideTyping();

        console.error(
            "Reply error:",
            error
        );

        addMessage(
            "Sorry, something went wrong. Please try again.",
            "vera"
        );

    } finally {

        isSending = false;

    }

}


/* =========================================================
   ROLE FOR THE CURRENT SCENARIO
   ========================================================= */

function currentFromRole() {

    // "performance" -> Vera talking to the merchant.
    // "appointment" / "customer" -> Vera talking to one of the merchant's
    // own customers (send_as: merchant_on_behalf on the backend).
    return currentScenario === "performance" ? "merchant" : "customer";
}


/* =========================================================
   CTA HANDLING
   ========================================================= */

function handleCTA(cta) {

    clearQuickReplies();


    /*
     * Yes / No buttons
     */

    if (cta === "binary_yes_no") {

        createQuickReply("Yes", "Yes");
        createQuickReply("No", "No");

    }


    /*
     * Confirm / Reschedule buttons (appointment reminders,
     * chronic-refill confirmations, etc.)
     */

    else if (cta === "binary_confirm_cancel") {

        createQuickReply("Confirm", "Confirm");
        createQuickReply("Reschedule", "Reschedule");

    }

}


/* =========================================================
   QUICK REPLY
   ========================================================= */

function createQuickReply(
    label,
    message
) {

    const button =
        document.createElement("button");

    button.className =
        "quick-reply";

    button.textContent =
        label;

    button.onclick = function () {

        sendMessage(
            message
        );

    };

    quickReplies.appendChild(
        button
    );

}


/* =========================================================
   CLEAR QUICK REPLIES
   ========================================================= */

function clearQuickReplies() {

    quickReplies.innerHTML = "";

}


/* =========================================================
   KEYBOARD
   ========================================================= */

function handleKeyDown(event) {

    if (
        event.key === "Enter" &&
        !event.shiftKey
    ) {

        event.preventDefault();

        sendMessage();

    }

}


/* =========================================================
   NEW CONVERSATION
   ========================================================= */

function newConversation() {

    conversationId = null;
    currentScenario = null;

    clearQuickReplies();

    messages.innerHTML = "";


    /*
     * Show welcome message.
     */

    addMessage(
        "Hi! I'm Vera. Please select a scenario to start a conversation.",
        "vera"
    );

}


/* =========================================================
   INITIAL STATE
   ========================================================= */

document.addEventListener(
    "DOMContentLoaded",
    function () {

        /*
         * Make sure the input starts empty.
         */

        input.value = "";

        clearQuickReplies();

    }
);
