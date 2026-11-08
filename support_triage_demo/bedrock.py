"""Opt-in Amazon Bedrock Converse adapter. boto3 is imported only when a live client is created."""

import math
import os

from .contract import ContractError
from .providers import ProviderError, ProviderTimeout

MODEL_ID_ENV = 'SUPPORT_TRIAGE_BEDROCK_MODEL_ID'
REGION_ENV = 'SUPPORT_TRIAGE_BEDROCK_REGION'
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 60.0
MAX_OUTPUT_TOKENS = 300
TIMEOUT_ERROR_NAMES = {'ReadTimeoutError', 'ConnectTimeoutError', 'TimeoutError'}


class BedrockConfigError(ValueError):
    pass


class BedrockConverseProvider:
    def __init__(self, model_id, region, timeout_seconds=DEFAULT_TIMEOUT_SECONDS, client=None, simulated=False):
        if not model_id:
            raise BedrockConfigError(f'Set a Bedrock model or inference profile ID (--model-id or {MODEL_ID_ENV}).')
        if not region:
            raise BedrockConfigError(f'Set the AWS Region for Bedrock (--region or {REGION_ENV}).')
        timeout_seconds = float(timeout_seconds)
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise BedrockConfigError(f'Timeout must be more than 0 and at most {MAX_TIMEOUT_SECONDS:g} seconds.')
        self.model_id, self.region, self.timeout_seconds = model_id, region, timeout_seconds
        self.client = client if client is not None else self._live_client()
        if simulated:
            self.info = {'id': 'bedrock-simulated', 'label': 'Amazon Bedrock (SIMULATED)', 'simulated': True,
                         'detail': 'Real Converse adapter code with a deterministic fake client. No AWS call.'}
        else:
            self.info = {'id': 'bedrock-live', 'label': 'Amazon Bedrock (LIVE, opt-in)', 'simulated': False,
                         'detail': f'Billable Converse calls: {model_id} in {region}, {timeout_seconds:g}s timeout.'}

    @classmethod
    def from_env(cls, model_id=None, region=None, timeout_seconds=DEFAULT_TIMEOUT_SECONDS):
        return cls(model_id or os.environ.get(MODEL_ID_ENV), region or os.environ.get(REGION_ENV), timeout_seconds)

    def _live_client(self):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as error:
            raise BedrockConfigError('Live Bedrock needs boto3: python3 -m pip install boto3') from error
        config = Config(region_name=self.region, connect_timeout=self.timeout_seconds,
                        read_timeout=self.timeout_seconds, retries={'total_max_attempts': 1})
        return boto3.client('bedrock-runtime', config=config)

    def complete(self, system_prompt, ticket_text):
        try:
            response = self.client.converse(
                modelId=self.model_id,
                system=[{'text': system_prompt}],
                messages=[{'role': 'user', 'content': [{'text': ticket_text}]}],
                inferenceConfig={'maxTokens': MAX_OUTPUT_TOKENS, 'temperature': 0},
            )
        except Exception as error:
            raise self._provider_error(error) from error
        return self._output_text(response)

    def _provider_error(self, error):
        names = {cls.__name__ for cls in type(error).__mro__}
        if names & TIMEOUT_ERROR_NAMES:
            return ProviderTimeout(f'No Bedrock response within the {self.timeout_seconds:g}s timeout '
                                   f'({type(error).__name__}).')
        response = getattr(error, 'response', None)
        code = response.get('Error', {}).get('Code') if isinstance(response, dict) else None
        return ProviderError(f'Bedrock {code or type(error).__name__}: {error}')

    @staticmethod
    def _output_text(response):
        if not isinstance(response, dict):
            raise ContractError('Bedrock response is not an object.')
        if response.get('stopReason') != 'end_turn':
            raise ContractError(f'Bedrock stopped with {response.get("stopReason")!r}, not a complete answer.')
        content = (response.get('output') or {}).get('message', {}).get('content') or []
        texts = [block['text'] for block in content if isinstance(block, dict) and isinstance(block.get('text'), str)]
        if not texts:
            raise ContractError('Bedrock response has no text content.')
        return ''.join(texts)
