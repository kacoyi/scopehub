"""Derive report claims from check results without loading the harness."""

SESSION_CHECKS = {
    'configured-model-session': ('configured_models', 'text'),
    'configured-voice-session': ('configured_models', 'voice'),
    'configured-market-source': ('real_market_data', 'market'),
}


def integration_validation(results):
    summary = {
        kind: {'status': 'not_evaluated', 'observations': []}
        for kind in ('configured_models', 'cloud_models', 'real_market_data')
    }
    summary['mock_ai_evaluated'] = False
    for row in results:
        if row['status'] != 'passed':
            continue
        check = row['id']
        if check in {'ai-workflow', 'ai-replay-evaluations'}:
            summary['mock_ai_evaluated'] = True
        if check in SESSION_CHECKS and 'observation' in row:
            kind, _ = SESSION_CHECKS[check]
            observation = row['observation']
            groups = [kind]
            if kind == 'configured_models' and observation['deployment'] == 'cloud':
                groups.append('cloud_models')
            for key in groups:
                summary[key]['status'] = 'observed'
                summary[key]['observations'].append(observation)
    return summary


def integration_summary_matches(summary, checks):
    expected = integration_validation(checks)
    return (isinstance(summary, dict) and summary.get('mock_ai_evaluated') is expected['mock_ai_evaluated']
            and summary == expected)
