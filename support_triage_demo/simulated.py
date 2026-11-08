"""Deterministic, offline stand-ins for model providers. No network calls; labeled SIMULATED in the UI."""

import json
import re

from .providers import SYSTEM_PROMPT

QUEUE_PATTERNS = {
    'billing': r'\bcharg|\brefund|\bpayment|\binvoice|\bbilled\b',
    'shipping': r'\bpackage|\btracking|\bdeliver|\bshipping\b|\barrived\b',
    'returns': r'\breturn|\bexchange|\bwrong size|\bdamaged\b',
    'technical': r'\bcrash|\berror\b|\bbug\b|\bapp\b',
    'account': r'\blog ?in\b|\bpassword|\baccount\b',
}
ESCALATION_PATTERN = r'\bchargeback|\blawyer|\blegal\b'
DEFAULT_PRIORITY = {'billing': 'high', 'technical': 'high', 'shipping': 'medium', 'account': 'medium',
                    'returns': 'low'}


def scripted_triage(ticket_text):
    """Keyword script shared by both simulators, so they differ only in wire format."""
    text = ticket_text.lower()
    hits = {queue: len(re.findall(pattern, text)) for queue, pattern in QUEUE_PATTERNS.items()}
    hits = {queue: count for queue, count in hits.items() if count}
    if re.search(ESCALATION_PATTERN, text):
        queue = max(hits, key=hits.get) if hits else 'general'
        return _result(queue, 'urgent', 'Customer mentions a chargeback or legal action.', True)
    if not hits:
        return _result('general', 'low', 'No clear intent; a person should follow up.', True)
    queue = max(hits, key=hits.get)
    if len(hits) > 1:
        others = ', '.join(sorted(set(hits) - {queue}))
        return _result(queue, 'medium', f'Ambiguous: matches {queue} and also {others}.', True)
    return _result(queue, DEFAULT_PRIORITY[queue], f'Clear {queue} request.', False)


def _result(queue, priority, reason, review_required):
    return {'queue': queue, 'priority': priority, 'reason': reason, 'review_required': review_required}


class SimulatedLegacyProvider:
    """Stands in for the pilot's existing (non-AWS) model provider and its response envelope."""

    info = {'id': 'legacy-simulated', 'label': 'Legacy provider (SIMULATED)', 'simulated': True,
            'detail': 'Deterministic keyword script in the legacy response format. No network calls.'}

    def complete(self, system_prompt, ticket_text):
        assert system_prompt == SYSTEM_PROMPT
        response = {'id': 'legacy-sim-1', 'choices': [{'text': json.dumps(scripted_triage(ticket_text))}]}
        return response['choices'][0]['text']
