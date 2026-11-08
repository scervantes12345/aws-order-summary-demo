"""Ticket workflow: triage with a provider, apply the review policy, then route or wait for a person."""

import threading

from .contract import ROUTABLE_QUEUES, ContractError, fail_safe, parse_output
from .providers import SYSTEM_PROMPT, ProviderError, ProviderTimeout
from .tickets import ticket_text


def triage(ticket, provider):
    """Return the triage record. Provider failures and invalid output fail safe to human review."""
    record = {'ticket_id': ticket['id'], 'provider': provider.info, 'error': None}
    try:
        result = parse_output(provider.complete(SYSTEM_PROMPT, ticket_text(ticket)))
    except ProviderTimeout as error:
        record['error'] = f'Timeout: {error}'
        result = fail_safe('Provider timed out; no automatic routing. Sent to human review.')
    except ProviderError as error:
        record['error'] = f'Provider error: {error}'
        result = fail_safe('Provider call failed; no automatic routing. Sent to human review.')
    except ContractError as error:
        record['error'] = f'Invalid output: {error}'
        result = fail_safe('Provider output failed validation; no automatic routing. Sent to human review.')
    except Exception as error:  # Unexpected adapter bugs must never auto-route a ticket.
        record['error'] = f'Unexpected {type(error).__name__}: {error}'
        result = fail_safe('Unexpected provider failure; no automatic routing. Sent to human review.')
    if result['priority'] == 'urgent' and not result['review_required']:
        result = {**result, 'review_required': True}
    record['result'] = result
    record['status'] = 'needs_review' if result['review_required'] else 'routed'
    record['fail_safe'] = record['error'] is not None
    return record


class ReviewDesk:
    """In-memory ticket state for the local demo: new -> routed, or new -> needs_review -> routed by a person."""

    def __init__(self, tickets):
        self.tickets = {ticket['id']: ticket for ticket in tickets}
        self._lock = threading.Lock()
        self._state = {}

    def get_ticket(self, ticket_id):
        return self.tickets.get(ticket_id)

    def record_triage(self, record):
        state = {'status': record['status'], 'triage': record, 'routed_to': None, 'routed_by': None}
        if record['status'] == 'routed':
            state.update(routed_to=record['result']['queue'], routed_by='automatic')
        with self._lock:
            self._state[record['ticket_id']] = state
        return state

    def review(self, ticket_id, queue):
        if queue not in ROUTABLE_QUEUES:
            raise ValueError(f'Queue must be one of: {", ".join(ROUTABLE_QUEUES)}.')
        with self._lock:
            state = self._state.get(ticket_id)
            if state is None:
                raise ValueError('Triage the ticket before reviewing it.')
            state.update(status='routed', routed_to=queue, routed_by='human reviewer')
            return state

    def snapshot(self):
        with self._lock:
            return [{**ticket, 'state': self._state.get(ticket_id, {'status': 'new'})}
                    for ticket_id, ticket in self.tickets.items()]

    def reset(self):
        with self._lock:
            self._state.clear()
