"""One-click evaluation against independently labeled synthetic tickets."""

import json

from .tickets import DATA_DIR
from .workflow import triage

DISCLAIMER = ('Integration checks only. Offline providers are SIMULATED with deterministic responses, so these '
              'results are not evidence of model quality, latency, or cost.')


def load_labels():
    return json.loads((DATA_DIR / 'eval_labels.json').read_text(encoding='utf-8'))


def check(label, result):
    checks = {
        'queue': result['queue'] in label['expected_queues'],
        'priority': result['priority'] == label['expected_priority'],
        'review_required': result['review_required'] == label['expected_review_required'],
    }
    return checks, all(checks.values())


def evaluate(providers, tickets, labels=None):
    labels = (labels or load_labels())['labels']
    rows = []
    for ticket in tickets:
        label = labels[ticket['id']]
        row = {'ticket_id': ticket['id'], 'subject': ticket['subject'], 'case': label['case'], 'expected': {
            'queue': label['expected_queues'], 'priority': label['expected_priority'],
            'review_required': label['expected_review_required']}, 'actual': {}}
        for provider in providers:
            record = triage(ticket, provider)
            checks, passed = check(label, record['result'])
            row['actual'][provider.info['id']] = {'result': record['result'], 'error': record['error'],
                                                  'checks': checks, 'passed': passed}
        rows.append(row)
    summary = {provider.info['id']: {'passed': sum(row['actual'][provider.info['id']]['passed'] for row in rows),
                                     'total': len(rows)} for provider in providers}
    return {'disclaimer': DISCLAIMER, 'providers': [provider.info for provider in providers], 'rows': rows,
            'summary': summary}
