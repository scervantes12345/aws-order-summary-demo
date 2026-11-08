"""Bounded tool dispatch loop shared by the simulated and the real Bedrock model.

Messages and tool specs use the Amazon Bedrock Converse API shapes, so either model
can drive the same loop. The model only *requests* tools; this loop validates each
request against the tool's JSON schema and runs the read-only Python tool itself.
"""

import time

from . import tools

MAX_MODEL_TURNS = 6
FINDINGS_TOOL = 'submit_findings'
SYSTEM_PROMPT = (
    'You are a sales anomaly investigator. Answer the question using only the provided read-only tools. '
    'Start with list_datasets, compare regional revenue, then attribute the change by product. '
    'Use the numbers exactly as the tools return them; never compute or invent figures. '
    'Finish by calling submit_findings exactly once. Put measured contributions in "observed". '
    'Put anything the available data cannot establish, such as the root cause and which evidence is missing, '
    'in "unknown". Do not present a cause as fact unless a tool result proves it.'
)
FINDINGS_SCHEMA = {
    'type': 'object',
    'properties': {
        'summary': {'type': 'string', 'maxLength': 600, 'description': 'One or two sentence answer.'},
        'observed': {
            'type': 'array', 'minItems': 1, 'maxItems': 6, 'items': {'type': 'string', 'maxLength': 400},
            'description': 'Facts measured by tool results, with their figures.',
        },
        'unknown': {
            'type': 'array', 'minItems': 1, 'maxItems': 6, 'items': {'type': 'string', 'maxLength': 400},
            'description': 'What the available data cannot establish, including missing evidence.',
        },
    },
    'required': ['summary', 'observed', 'unknown'],
    'additionalProperties': False,
}


def all_tool_specs():
    specs = tools.tool_specs()
    specs[FINDINGS_TOOL] = {
        'description': 'Submit the final findings. Call once, after the data tools.',
        'schema': FINDINGS_SCHEMA,
    }
    return specs


def converse_tool_config():
    return {'tools': [
        {'toolSpec': {'name': name, 'description': spec['description'], 'inputSchema': {'json': spec['schema']}}}
        for name, spec in all_tool_specs().items()
    ]}


def _check(value, schema, path):
    kind = schema.get('type')
    if kind == 'object':
        if not isinstance(value, dict):
            raise tools.ToolInputError(f'{path} must be an object.')
        properties = schema.get('properties', {})
        missing = [key for key in schema.get('required', []) if key not in value]
        if missing:
            raise tools.ToolInputError(f'{path} is missing required field(s): {", ".join(missing)}.')
        extra = [key for key in value if key not in properties]
        if extra and schema.get('additionalProperties') is False:
            raise tools.ToolInputError(f'{path} has unexpected field(s): {", ".join(sorted(extra))}.')
        for key, item in value.items():
            _check(item, properties[key], f'{path}.{key}')
    elif kind == 'array':
        if not isinstance(value, list):
            raise tools.ToolInputError(f'{path} must be an array.')
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', len(value)):
            raise tools.ToolInputError(f'{path} has {len(value)} item(s), outside the allowed range.')
        for index, item in enumerate(value):
            _check(item, schema['items'], f'{path}[{index}]')
    elif kind == 'string':
        if not isinstance(value, str):
            raise tools.ToolInputError(f'{path} must be a string.')
        if 'enum' in schema and value not in schema['enum']:
            raise tools.ToolInputError(f'{path} must be one of: {", ".join(schema["enum"])}.')
        if len(value) > schema.get('maxLength', len(value)):
            raise tools.ToolInputError(f'{path} is longer than {schema["maxLength"]} characters.')


def validate_tool_request(name, tool_input):
    """Reject unknown tool names and inputs that do not match the tool's schema."""
    specs = all_tool_specs()
    if not isinstance(name, str) or name not in specs:
        raise tools.ToolInputError(f'Unknown tool {name!r}. Allowed tools: {", ".join(specs)}.')
    _check(tool_input, specs[name]['schema'], 'input')


def run_investigation(question, model, max_turns=MAX_MODEL_TURNS):
    """Run the model/tool loop for at most ``max_turns`` model turns and return answer plus trace."""
    messages = [{'role': 'user', 'content': [{'text': question}]}]
    tool_config = converse_tool_config()
    trace = []
    for turn in range(1, max_turns + 1):
        response = model.converse(system=SYSTEM_PROMPT, messages=messages, tool_config=tool_config)
        message = response['output']['message']
        messages.append(message)
        tool_uses = [block['toolUse'] for block in message.get('content', []) if 'toolUse' in block]
        if not tool_uses:
            text = ' '.join(block['text'] for block in message.get('content', []) if 'text' in block).strip()
            return _result(question, model, 'incomplete', turn, max_turns, trace, None,
                           text or 'The model stopped without submitting findings.')
        results = []
        for tool_use in tool_uses:
            name, tool_input = tool_use.get('name'), tool_use.get('input')
            step = {'step': len(trace) + 1, 'turn': turn, 'tool': name, 'input': tool_input}
            trace.append(step)
            try:
                validate_tool_request(name, tool_input)
            except tools.ToolInputError as error:
                step.update(status='rejected', error=str(error))
                results.append(_tool_result(tool_use, {'error': str(error)}, 'error'))
                continue
            if name == FINDINGS_TOOL:
                step.update(status='final', result=tool_input)
                return _result(question, model, 'complete', turn, max_turns, trace, tool_input, None)
            started = time.perf_counter()
            try:
                output = tools.DATA_TOOLS[name](**tool_input)
            except tools.ToolInputError as error:
                step.update(status='error', error=str(error))
                results.append(_tool_result(tool_use, {'error': str(error)}, 'error'))
                continue
            step.update(status='ok', result=output, duration_ms=round((time.perf_counter() - started) * 1000, 2))
            results.append(_tool_result(tool_use, output, 'success'))
        messages.append({'role': 'user', 'content': results})
    return _result(question, model, 'incomplete', max_turns, max_turns, trace, None,
                   f'Stopped after {max_turns} model turns without findings.')


def _tool_result(tool_use, payload, status):
    return {'toolResult': {'toolUseId': tool_use.get('toolUseId'), 'content': [{'json': payload}], 'status': status}}


def _result(question, model, status, turns, max_turns, trace, findings, error):
    return {
        'question': question,
        'model': model.info,
        'status': status,
        'model_turns': turns,
        'max_model_turns': max_turns,
        'findings': findings,
        'error': error,
        'trace': trace,
    }
