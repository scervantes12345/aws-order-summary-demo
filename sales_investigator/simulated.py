"""Deterministic stand-in for a Bedrock model, used in offline mode.

It returns Converse-shaped responses: tool requests first, then submit_findings built
only from the tool results already in the conversation. It does no arithmetic of its
own; it only picks the next tool and formats the figures the tools returned.
"""

import re
from decimal import Decimal

MISSING_EVIDENCE = {'inventory': 'inventory'}


def _usd(value, signed=False):
    amount = Decimal(value)
    text = f'${abs(amount):,.2f}'
    if not signed:
        return text
    return ('+' if amount > 0 else '\u2212' if amount < 0 else '') + text


def _pct(value, signed=False):
    if value is None:
        return 'n/a'
    amount = Decimal(value)
    sign = ('+' if amount > 0 else '\u2212' if amount < 0 else '') if signed else ''
    return f'{sign}{abs(amount)}%'


class SimulatedBedrockModel:
    info = {
        'provider': 'simulated',
        'label': 'Simulated Bedrock',
        'model_id': 'simulated-bedrock-deterministic-v1',
        'description': 'Offline, deterministic stand-in for an Amazon Bedrock model. No AWS calls are made.',
    }

    def converse(self, system, messages, tool_config):
        allowed = {tool['toolSpec']['name'] for tool in tool_config['tools']}
        question = messages[0]['content'][0]['text']
        results, last_status = self._tool_results(messages)
        if last_status and last_status[0] == 'error':
            return self._text(f'Simulated model stopped: a tool returned an error ({last_status[1]}).')
        if 'list_datasets' not in results:
            return self._tool_use(messages, 'list_datasets', {})
        dataset = results['list_datasets']['datasets'][0]
        baseline_date, current_date = dataset['dates'][-2], dataset['dates'][-1]
        if 'compare_region_revenue' not in results:
            return self._tool_use(messages, 'compare_region_revenue',
                                  {'baseline_date': baseline_date, 'current_date': current_date})
        comparison = results['compare_region_revenue']
        region = self._pick_region(question, comparison['regions'])
        if 'attribute_change_by_product' not in results:
            return self._tool_use(messages, 'attribute_change_by_product',
                                  {'region': region, 'baseline_date': baseline_date, 'current_date': current_date})
        if 'submit_findings' not in allowed:
            return self._text('No submit_findings tool is available.')
        findings = self._findings(results['list_datasets'], comparison, results['attribute_change_by_product'])
        return self._tool_use(messages, 'submit_findings', findings)

    @staticmethod
    def _tool_results(messages):
        names, results, last_status = {}, {}, None
        for message in messages:
            for block in message['content']:
                if 'toolUse' in block:
                    names[block['toolUse']['toolUseId']] = block['toolUse']['name']
                if 'toolResult' in block:
                    result = block['toolResult']
                    payload = result['content'][0]['json']
                    last_status = (result.get('status', 'success'), payload.get('error'))
                    if last_status[0] == 'success':
                        results[names[result['toolUseId']]] = payload
        return results, last_status

    @staticmethod
    def _pick_region(question, comparison):
        mentions = []
        for row in comparison:
            match = re.search(rf'\b{re.escape(row["region"])}\b', question, re.IGNORECASE)
            if match:
                mentions.append((match.start(), row['region']))
        if mentions:
            return min(mentions)[1]
        return min(comparison, key=lambda row: Decimal(row['change_usd']))['region']

    @staticmethod
    def _findings(catalog, comparison, attribution):
        region = attribution['region']
        row = next(item for item in comparison['regions'] if item['region'] == region)
        before, after = comparison['baseline_date'], comparison['current_date']
        change = Decimal(row['change_usd'])
        direction = 'decline' if change < 0 else 'increase'
        top, *rest = attribution['products']
        if change < 0:
            headline = f'{region} revenue fell {_usd(row["change_usd"])} ({_pct(row["change_pct"])})'
        elif change > 0:
            headline = f'{region} revenue did not drop; it rose {_usd(row["change_usd"])} ({_pct(row["change_pct"])})'
        else:
            headline = f'{region} revenue was unchanged'
        summary = (
            f'{headline}, from {_usd(row["baseline_revenue_usd"])} on {before} to '
            f'{_usd(row["current_revenue_usd"])} on {after}.'
        )
        observed = [
            f'{region} revenue: {_usd(row["baseline_revenue_usd"])} on {before} \u2192 '
            f'{_usd(row["current_revenue_usd"])} on {after} '
            f'({_usd(row["change_usd"], True)}, {_pct(row["change_pct"], True)}).'
        ]
        if change != 0:
            summary += (
                f' {top["product"]} accounts for {_usd(top["change_usd"])} of the {_usd(row["change_usd"])} '
                f'{direction}; the available data does not show why.'
            )
            observed.append(
                f'{top["product"]}: {_usd(top["baseline_revenue_usd"])} \u2192 {_usd(top["current_revenue_usd"])} '
                f'({_usd(top["change_usd"], True)}), {_pct(top["share_of_region_change_pct"])} of {region}\'s change.'
            )
            observed.append(f'Other {region} products: ' + ', '.join(
                f'{item["product"]} {_usd(item["change_usd"], True)} ({_pct(item["share_of_region_change_pct"])})'
                for item in rest) + '.')
        others = [item for item in comparison['regions'] if item['region'] != region]
        if others:
            observed.append('Other regions: ' + ', '.join(
                f'{item["region"]} {_usd(item["change_usd"], True)} ({_pct(item["change_pct"], True)})'
                for item in others) + '.')
        available = [dataset['name'] for dataset in catalog['datasets']]
        unknown = [
            f'Root cause: unknown. Sales data shows where the {direction} happened, '
            f'not why {top["product"]} sales changed.'
        ]
        for keyword, label in MISSING_EVIDENCE.items():
            if not any(keyword in name for name in available):
                unknown.append(
                    f'Missing evidence: no {label} data is available (datasets: {", ".join(available)}), '
                    f'so a {top["product"]} stockout in {region} cannot be confirmed or ruled out.'
                )
        return {'summary': summary, 'observed': observed, 'unknown': unknown}

    @staticmethod
    def _tool_use(messages, name, tool_input):
        number = sum(1 for message in messages if message['role'] == 'assistant') + 1
        return {
            'output': {'message': {'role': 'assistant', 'content': [
                {'toolUse': {'toolUseId': f'sim-tooluse-{number}', 'name': name, 'input': tool_input}}
            ]}},
            'stopReason': 'tool_use',
        }

    @staticmethod
    def _text(text):
        return {'output': {'message': {'role': 'assistant', 'content': [{'text': text}]}}, 'stopReason': 'end_turn'}
