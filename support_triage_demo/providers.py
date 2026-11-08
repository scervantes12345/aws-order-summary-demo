"""Provider interface: complete(system_prompt, ticket_text) returns the model's raw text."""

SYSTEM_PROMPT = (
    'You route retail customer support tickets. Reply with only a JSON object with exactly these keys: '
    '"queue" (one of: billing, shipping, returns, technical, account, general), '
    '"priority" (one of: low, medium, high, urgent), '
    '"reason" (one short sentence, at most 300 characters), '
    '"review_required" (true if the ticket is ambiguous, mentions legal action or a chargeback, '
    'or has no clear intent; otherwise false). Do not include any other text.'
)


class ProviderError(RuntimeError):
    """The provider call failed. The workflow routes the ticket to human review."""


class ProviderTimeout(ProviderError):
    """The provider did not answer within its configured timeout."""
