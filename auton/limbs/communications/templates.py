"""Jinja2 templates for human-in-the-loop approval emails."""

from __future__ import annotations

from typing import Any

from jinja2 import Template


# ------------------------------------------------------------------ #
# Plain-text templates
# ------------------------------------------------------------------ #

TRADE_PROPOSAL_TXT = Template(
    """\
Action Proposal: {{ proposal.what }}
Approval Token: {{ proposal.approval_token }}

WHAT
----
{{ proposal.what }}

WHY
---
{{ proposal.why }}

RISK
----
{{ proposal.risk }}

EXPECTED OUTCOME
---------------
{{ proposal.expected_outcome }}

URGENCY: {{ proposal.urgency }}
Timestamp: {{ proposal.timestamp.isoformat() }}

To approve, reply with:
APPROVE {{ proposal.approval_token }}

To deny, reply with:
DENY {{ proposal.approval_token }}
"""
)

DEPLOYMENT_PROPOSAL_TXT = Template(
    """\
Deployment Proposal: {{ proposal.what }}
Approval Token: {{ proposal.approval_token }}

WHAT
----
{{ proposal.what }}

WHY
---
{{ proposal.why }}

RISK
----
{{ proposal.risk }}

EXPECTED OUTCOME
---------------
{{ proposal.expected_outcome }}

URGENCY: {{ proposal.urgency }}
Timestamp: {{ proposal.timestamp.isoformat() }}

To approve, reply with:
APPROVE {{ proposal.approval_token }}

To deny, reply with:
DENY {{ proposal.approval_token }}
"""
)

GENERIC_ACTION_PROPOSAL_TXT = Template(
    """\
Action Proposal: {{ proposal.what }}
Approval Token: {{ proposal.approval_token }}

WHAT
----
{{ proposal.what }}

WHY
---
{{ proposal.why }}

RISK
----
{{ proposal.risk }}

EXPECTED OUTCOME
---------------
{{ proposal.expected_outcome }}

URGENCY: {{ proposal.urgency }}
Timestamp: {{ proposal.timestamp.isoformat() }}

To approve, reply with:
APPROVE {{ proposal.approval_token }}

To deny, reply with:
DENY {{ proposal.approval_token }}
"""
)


# ------------------------------------------------------------------ #
# HTML templates
# ------------------------------------------------------------------ #

TRADE_PROPOSAL_HTML = Template(
    """\
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
body { font-family: sans-serif; line-height: 1.5; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }
h1 { font-size: 1.4em; border-bottom: 2px solid #2c3e50; padding-bottom: 8px; }
h2 { font-size: 1.1em; color: #2c3e50; margin-top: 24px; }
.token { font-family: monospace; background: #f4f4f4; padding: 4px 8px; border-radius: 4px; }
.urgency-low { color: #27ae60; }
.urgency-medium { color: #f39c12; }
.urgency-high { color: #e67e22; }
.urgency-critical { color: #c0392b; font-weight: bold; }
.section { margin: 16px 0; }
.footer { margin-top: 32px; font-size: 0.9em; color: #666; border-top: 1px solid #ddd; padding-top: 12px; }
</style>
</head>
<body>
<h1>Trade Proposal</h1>
<p><strong>Approval Token:</strong> <span class="token">{{ proposal.approval_token }}</span></p>
<div class="section"><h2>What</h2><p>{{ proposal.what }}</p></div>
<div class="section"><h2>Why</h2><p>{{ proposal.why }}</p></div>
<div class="section"><h2>Risk</h2><p>{{ proposal.risk }}</p></div>
<div class="section"><h2>Expected Outcome</h2><p>{{ proposal.expected_outcome }}</p></div>
<p><strong>Urgency:</strong> <span class="urgency-{{ proposal.urgency.lower() }}">{{ proposal.urgency.upper() }}</span></p>
<div class="footer">
<p>Timestamp: {{ proposal.timestamp.isoformat() }}</p>
<p>To approve, reply with: <code>APPROVE {{ proposal.approval_token }}</code></p>
<p>To deny, reply with: <code>DENY {{ proposal.approval_token }}</code></p>
</div>
</body>
</html>
"""
)

DEPLOYMENT_PROPOSAL_HTML = Template(
    """\
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
body { font-family: sans-serif; line-height: 1.5; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }
h1 { font-size: 1.4em; border-bottom: 2px solid #8e44ad; padding-bottom: 8px; }
h2 { font-size: 1.1em; color: #8e44ad; margin-top: 24px; }
.token { font-family: monospace; background: #f4f4f4; padding: 4px 8px; border-radius: 4px; }
.urgency-low { color: #27ae60; }
.urgency-medium { color: #f39c12; }
.urgency-high { color: #e67e22; }
.urgency-critical { color: #c0392b; font-weight: bold; }
.section { margin: 16px 0; }
.footer { margin-top: 32px; font-size: 0.9em; color: #666; border-top: 1px solid #ddd; padding-top: 12px; }
</style>
</head>
<body>
<h1>Deployment Proposal</h1>
<p><strong>Approval Token:</strong> <span class="token">{{ proposal.approval_token }}</span></p>
<div class="section"><h2>What</h2><p>{{ proposal.what }}</p></div>
<div class="section"><h2>Why</h2><p>{{ proposal.why }}</p></div>
<div class="section"><h2>Risk</h2><p>{{ proposal.risk }}</p></div>
<div class="section"><h2>Expected Outcome</h2><p>{{ proposal.expected_outcome }}</p></div>
<p><strong>Urgency:</strong> <span class="urgency-{{ proposal.urgency.lower() }}">{{ proposal.urgency.upper() }}</span></p>
<div class="footer">
<p>Timestamp: {{ proposal.timestamp.isoformat() }}</p>
<p>To approve, reply with: <code>APPROVE {{ proposal.approval_token }}</code></p>
<p>To deny, reply with: <code>DENY {{ proposal.approval_token }}</code></p>
</div>
</body>
</html>
"""
)

GENERIC_ACTION_PROPOSAL_HTML = Template(
    """\
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
body { font-family: sans-serif; line-height: 1.5; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }
h1 { font-size: 1.4em; border-bottom: 2px solid #2980b9; padding-bottom: 8px; }
h2 { font-size: 1.1em; color: #2980b9; margin-top: 24px; }
.token { font-family: monospace; background: #f4f4f4; padding: 4px 8px; border-radius: 4px; }
.urgency-low { color: #27ae60; }
.urgency-medium { color: #f39c12; }
.urgency-high { color: #e67e22; }
.urgency-critical { color: #c0392b; font-weight: bold; }
.section { margin: 16px 0; }
.footer { margin-top: 32px; font-size: 0.9em; color: #666; border-top: 1px solid #ddd; padding-top: 12px; }
</style>
</head>
<body>
<h1>Action Proposal</h1>
<p><strong>Approval Token:</strong> <span class="token">{{ proposal.approval_token }}</span></p>
<div class="section"><h2>What</h2><p>{{ proposal.what }}</p></div>
<div class="section"><h2>Why</h2><p>{{ proposal.why }}</p></div>
<div class="section"><h2>Risk</h2><p>{{ proposal.risk }}</p></div>
<div class="section"><h2>Expected Outcome</h2><p>{{ proposal.expected_outcome }}</p></div>
<p><strong>Urgency:</strong> <span class="urgency-{{ proposal.urgency.lower() }}">{{ proposal.urgency.upper() }}</span></p>
<div class="footer">
<p>Timestamp: {{ proposal.timestamp.isoformat() }}</p>
<p>To approve, reply with: <code>APPROVE {{ proposal.approval_token }}</code></p>
<p>To deny, reply with: <code>DENY {{ proposal.approval_token }}</code></p>
</div>
</body>
</html>
"""
)


_TEMPLATE_MAP: dict[str, dict[str, Template]] = {
    "trade": {
        "html": TRADE_PROPOSAL_HTML,
        "txt": TRADE_PROPOSAL_TXT,
    },
    "deployment": {
        "html": DEPLOYMENT_PROPOSAL_HTML,
        "txt": DEPLOYMENT_PROPOSAL_TXT,
    },
    "generic": {
        "html": GENERIC_ACTION_PROPOSAL_HTML,
        "txt": GENERIC_ACTION_PROPOSAL_TXT,
    },
}


def get_templates(action_type: str) -> dict[str, Template]:
    """Return the HTML and plain-text templates for *action_type*."""
    return _TEMPLATE_MAP.get(action_type, _TEMPLATE_MAP["generic"])


def render_proposal(proposal: Any) -> dict[str, str]:
    """Render both HTML and plain-text bodies for a proposal.

    Args:
        proposal: An object with ``action_type``, ``what``, ``why``,
            ``risk``, ``expected_outcome``, ``urgency``,
            ``approval_token``, and ``timestamp`` attributes.

    Returns:
        A dict with ``html`` and ``text`` keys.
    """
    templates = get_templates(proposal.action_type)
    return {
        "html": templates["html"].render(proposal=proposal),
        "text": templates["txt"].render(proposal=proposal),
    }
