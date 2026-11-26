"""Derive report claims from check results without loading the harness."""

SESSION_CHECKS = {
    'configured-model-session': ('configured_models', 'text'),
    'configured-voice-session': ('configured_models', 'voice'),
    'configured-market-source': ('real_market_data', 'market'),
}
EXTERNAL_CLIENT_CHECK = 'configured-external-ai-clients'
RUN_OBSERVATION_CHECKS = SESSION_CHECKS.keys() | {EXTERNAL_CLIENT_CHECK}
RUN_OBSERVATION_POLICIES = {
    'session': frozenset(SESSION_CHECKS),
    'session-and-external-clients': frozenset(RUN_OBSERVATION_CHECKS),
}
PHYSICAL_WINDOWS_CHECK = 'physical-windows-workspace'
UBUNTU_WORKSPACE_CHECK = 'ubuntu-workspace-observation'
FIELD_OBSERVATION_CHECKS = {PHYSICAL_WINDOWS_CHECK, UBUNTU_WORKSPACE_CHECK}
OBSERVATION_CHECKS = RUN_OBSERVATION_CHECKS | FIELD_OBSERVATION_CHECKS


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


def passed_observation_checks(checks):
    return {row['id'] for row in checks if row['status'] == 'passed' and row['id'] in OBSERVATION_CHECKS}


def publishing_observation_checks(catalog, run_observations):
    owners = run_observations | FIELD_OBSERVATION_CHECKS
    return {row['id'] for row in catalog['checks'] if row['id'] in owners and row['status'] == 'implemented'}


def report_observation_issue(report, *, run_observations=RUN_OBSERVATION_CHECKS):
    """Validate observation claims available in both raw publication and timing inputs."""
    checks = report['checks']
    for row in checks:
        check, passed = row['id'], row['status'] == 'passed'
        for field, owners in (('observation', run_observations),
                              ('field_observation', FIELD_OBSERVATION_CHECKS)):
            expected = passed and check in owners
            if field in row and (not expected or not isinstance(row[field], dict)):
                return 'an unexpected observation'
            if expected and field not in row:
                return 'a passing check without its observation'
        if passed and check in SESSION_CHECKS and SESSION_CHECKS[check][0] == 'configured_models':
            if row['observation'].get('deployment') not in ('local', 'cloud'):
                return 'a configured model observation without a valid deployment'
    if ('integration_validation' in report
            and not integration_summary_matches(report['integration_validation'], checks)):
        return 'an integration summary that contradicts its check results'
    return None
