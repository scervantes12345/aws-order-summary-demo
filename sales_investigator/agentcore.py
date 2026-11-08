"""Invocation handler used by the AgentCore Runtime entry point (agentcore_runtime/main.py)."""

from . import DEFAULT_QUESTION
from .agent import run_investigation
from .bedrock import BedrockConverseModel
from .simulated import SimulatedBedrockModel

MAX_PROMPT_LENGTH = 300


def handle_invocation(payload, bedrock_factory=BedrockConverseModel.from_env):
    """Payload: {"prompt": str, "model_id": optional str, "simulated": optional bool}."""
    payload = payload if isinstance(payload, dict) else {}
    question = payload.get('prompt') or DEFAULT_QUESTION
    if not isinstance(question, str) or len(question) > MAX_PROMPT_LENGTH:
        return {'status': 'error', 'error': f'"prompt" must be a string of at most {MAX_PROMPT_LENGTH} characters.'}
    if payload.get('simulated') is True:
        model = SimulatedBedrockModel()
    else:
        model = bedrock_factory(model_id=payload.get('model_id'))
    return run_investigation(question.strip(), model)
