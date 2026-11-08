"""Provider-independent output contract shared by every triage provider."""

import json

ROUTABLE_QUEUES = ('billing', 'shipping', 'returns', 'technical', 'account', 'general')
HUMAN_REVIEW_QUEUE = 'human_review'
QUEUES = ROUTABLE_QUEUES + (HUMAN_REVIEW_QUEUE,)
PRIORITIES = ('low', 'medium', 'high', 'urgent')
FIELDS = ('queue', 'priority', 'reason', 'review_required')
MAX_REASON_LENGTH = 300


class ContractError(ValueError):
    """The provider output does not match the triage contract."""


def parse_output(text):
    """Parse a provider's JSON text into a validated contract dict."""
    if not isinstance(text, str):
        raise ContractError('Provider output must be text.')
    stripped = text.strip()
    if stripped.startswith('```'):
        stripped = stripped.strip('`').removeprefix('json').strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as error:
        raise ContractError(f'Provider output is not valid JSON ({error.msg}).') from None
    return validate(data)


def validate(data):
    if not isinstance(data, dict):
        raise ContractError('Provider output must be a JSON object.')
    if set(data) != set(FIELDS):
        missing = sorted(set(FIELDS) - set(data))
        extra = sorted(set(data) - set(FIELDS))
        raise ContractError(f'Provider output fields are wrong (missing: {missing}, unexpected: {extra}).')
    if data['queue'] not in ROUTABLE_QUEUES:
        raise ContractError(f'Queue {data["queue"]!r} is not an allowed queue.')
    if data['priority'] not in PRIORITIES:
        raise ContractError(f'Priority {data["priority"]!r} is not an allowed priority.')
    reason = data['reason']
    if not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_REASON_LENGTH:
        raise ContractError(f'Reason must be non-empty text of at most {MAX_REASON_LENGTH} characters.')
    if not isinstance(data['review_required'], bool):
        raise ContractError('review_required must be true or false.')
    return {'queue': data['queue'], 'priority': data['priority'], 'reason': reason.strip(),
            'review_required': data['review_required']}


def fail_safe(reason):
    """Route to human review when a provider fails or its output cannot be trusted."""
    return {'queue': HUMAN_REVIEW_QUEUE, 'priority': 'high', 'reason': reason, 'review_required': True}
