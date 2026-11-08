"""Opt-in Amazon Bedrock Converse adapter. Requires boto3 and configured AWS credentials."""

import os

MODEL_ID_ENV = 'BEDROCK_MODEL_ID'


class BedrockConfigError(RuntimeError):
    pass


class BedrockConverseModel:
    def __init__(self, model_id, client=None, region_name=None, max_tokens=1500):
        if not model_id:
            raise BedrockConfigError(f'Set a Bedrock model ID (for example with the {MODEL_ID_ENV} environment variable).')
        if client is None:
            try:
                import boto3
            except ImportError as error:
                raise BedrockConfigError('Bedrock mode needs boto3: python3 -m pip install boto3') from error
            client = boto3.client('bedrock-runtime', region_name=region_name)
        self.client = client
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.info = {
            'provider': 'bedrock',
            'label': 'Amazon Bedrock',
            'model_id': model_id,
            'description': 'Live Amazon Bedrock Converse API calls using your configured AWS credentials.',
        }

    @classmethod
    def from_env(cls, model_id=None, **kwargs):
        return cls(model_id or os.environ.get(MODEL_ID_ENV), **kwargs)

    def converse(self, system, messages, tool_config):
        return self.client.converse(
            modelId=self.model_id,
            system=[{'text': system}],
            messages=messages,
            toolConfig=tool_config,
            inferenceConfig={'maxTokens': self.max_tokens, 'temperature': 0},
        )
