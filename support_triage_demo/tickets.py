"""Synthetic support tickets. None of these are real customers or real ticket data."""

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / 'data'


def load_tickets():
    return json.loads((DATA_DIR / 'tickets.json').read_text(encoding='utf-8'))


def ticket_text(ticket):
    return f'Subject: {ticket["subject"]}\n\n{ticket["body"]}'
