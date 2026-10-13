"""Assemble the importable n8n workflow from the two Code node scripts in code/.

Usage:
    python integrations/n8n/build_workflow.py

The JavaScript lives in code/*.js so it can be read, diffed and tested
(tests/test_n8n.py runs it in Node); this script embeds it into
werkstudent-radar-alerts.json. Run it after editing a script; a test fails when
the JSON is out of date. The file holds no secrets: the webhook secret comes from
n8n's environment and the bot token from an n8n credential.
"""

import json
import os
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "werkstudent-radar-alerts.json")
WEBHOOK_PATH = "werkstudent-radar"
NS = uuid.UUID("6f1d3c2e-0b7a-4c1e-9a55-2d0c5e8b7a10")    # stable node ids, so rebuilding changes nothing


def code(name):
    with open(os.path.join(HERE, "code", f"{name}.js"), encoding="utf-8") as f:
        return f.read()


def node(name, type_, version, position, parameters, **extra):
    return {"parameters": parameters, "id": str(uuid.uuid5(NS, name)), "name": name, "type": type_,
            "typeVersion": version, "position": position, **extra}


def is_true(name, left):
    """An If node condition: `left` is boolean true."""
    return {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
            "conditions": [{"id": str(uuid.uuid5(NS, name + ":cond")), "leftValue": left, "rightValue": "",
                            "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
            "combinator": "and",
        },
        "options": {},
    }


def respond(name, position, code_expr, body_expr):
    return node(name, "n8n-nodes-base.respondToWebhook", 1.4, position, {
        "respondWith": "json",
        "responseBody": body_expr,
        "options": {"responseCode": code_expr},
    })


def telegram(name, position):
    return node(name, "n8n-nodes-base.telegram", 1.2, position, {
        "chatId": "={{ $json.chatId }}",
        "text": "={{ $json.text }}",
        "additionalFields": {"appendAttribution": False, "disable_web_page_preview": True, "parse_mode": "HTML"},
    }, credentials={"telegramApi": {"id": "", "name": "Werkstudent Radar bot"}})


def workflow():
    nodes = [
        node("Webhook", "n8n-nodes-base.webhook", 2.1, [0, 300], {
            "httpMethod": "POST",
            "path": WEBHOOK_PATH,
            "responseMode": "responseNode",
            "options": {"rawBody": True},
        }, webhookId=str(uuid.uuid5(NS, "webhook"))),
        node("Check signature", "n8n-nodes-base.code", 2, [220, 300], {"jsCode": code("check_signature")}),
        node("Valid?", "n8n-nodes-base.if", 2.2, [440, 300], is_true("Valid?", "={{ $json.ok }}")),
        respond("Accept", [660, 200], 200, '={{ { "ok": true } }}'),
        respond("Reject", [660, 420], "={{ $json.status }}", '={{ { "ok": false, "reason": $json.reason } }}'),
        node("Build messages", "n8n-nodes-base.code", 2, [880, 200], {"jsCode": code("build_messages")}),
        node("Public channel?", "n8n-nodes-base.if", 2.2, [1100, 200], {
            **is_true("Public channel?", "={{ $json.target === 'channel' }}"),
        }),
        telegram("Telegram: public channel", [1320, 100]),
        telegram("Telegram: my chat", [1320, 300]),
    ]
    main = lambda *targets: {"main": [[{"node": t, "type": "main", "index": 0} for t in out] for out in targets]}
    connections = {
        "Webhook": main(["Check signature"]),
        "Check signature": main(["Valid?"]),
        "Valid?": main(["Accept"], ["Reject"]),
        "Accept": main(["Build messages"]),
        "Build messages": main(["Public channel?"]),
        "Public channel?": main(["Telegram: public channel"], ["Telegram: my chat"]),
    }
    return {
        "name": "Werkstudent Radar: Telegram alerts",
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1", "timezone": "Europe/Berlin"},
        "pinData": {},
        "active": False,
    }


def render():
    return json.dumps(workflow(), ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(render())
    print(f"wrote {os.path.relpath(OUT)}")
