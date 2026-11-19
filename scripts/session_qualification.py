"""Score the redacted results of a real, explicitly authorized synthetic-data session."""
from decimal import Decimal, InvalidOperation
from math import isfinite
import re
from uuid import UUID


def validate_private_transfer(observation, answers, probe):
    measured = answers.get('private_transfer')
    if not isinstance(measured, dict) or set(measured) != {
        'completed_ordinary_turns', 'repeated_permission_prompts', 'private_egress',
        'model_visible_private', 'reused_private_context'
    }:
        raise ValueError('Missing redacted private-transfer observations')
    for field, expected in [('completed_ordinary_turns', 2), ('repeated_permission_prompts', 0)]:
        if type(measured[field]) is not int or measured[field] != expected:
            raise ValueError('Ordinary turns must complete under the connection grant')
    deployment = observation.get('deployment')
    if deployment not in {'local', 'cloud'}:
        raise ValueError('Private-transfer observations need the actual local or cloud deployment')
    expected_egress = {stage: [] for stage in probe['stages']}
    if deployment == 'cloud':
        expected_egress['selected'] = [probe['selected_sha256']]
    if measured['private_egress'] != expected_egress:
        raise ValueError('Observed private egress differs from the selected bytes and destination')
    expected_visible = {stage: [] for stage in probe['stages']}
    expected_visible['selected'] = [probe['selected_sha256']]
    if measured['model_visible_private'] != expected_visible:
        raise ValueError('The model could access private content outside the selected request or profile')
    if measured['reused_private_context'] is not False:
        raise ValueError('The follow-up reused a context handle or history containing the private selection')


def validate_qualification(observation, case):
    """Return the case ID only after the reported fields, effects and limits match."""
    answers = observation.get('qualification')
    if not isinstance(answers, dict) or answers.get('case') != case['id']:
        raise ValueError('Missing the fixed session qualification case')
    allowed = {'case', 'private_transfer', 'confirmed', 'initial', 'corrected', 'effects',
               'approved_annotation', 'billing', 'voice'}
    if set(answers) - allowed:
        raise ValueError('Unexpected fields in a redacted session qualification')
    validate_private_transfer(observation, answers, case['private_transfer'])
    if answers.get('confirmed') != case['confirmed']:
        raise ValueError('The confirmed instrument, date, time zone, price or quantity differs')
    digests = set()
    for key in ('initial', 'corrected'):
        measured = answers.get(key)
        if not isinstance(measured, dict) or measured != case[key]:
            raise ValueError(f'Incorrect {key} interval or calculation')
        digest = measured.get('snapshot')
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('The calculation needs its observed immutable input digest')
        digests.add(digest)
    if len(digests) != 2:
        raise ValueError('The revised interval reused the original calculation input')
    if answers.get('effects') != case['effects']:
        raise ValueError('A denied, ambiguous or redirected request produced an unexpected effect')
    receipt = answers.get('approved_annotation')
    if not isinstance(receipt, dict) or receipt.get('target') != case['confirmed']['instrument'] or receipt.get('commits') != 1:
        raise ValueError('Missing the single authorized annotation on the selected chart')
    if set(receipt) != {'target', 'commits', 'operation_id'}:
        raise ValueError('Unexpected fields in the redacted annotation receipt')
    operation_id = receipt.get('operation_id')
    if not isinstance(operation_id, str) or not re.fullmatch(r'[0-9a-fA-F-]{36}', operation_id):
        raise ValueError('Missing the observed core operation receipt')
    if str(UUID(operation_id)) != operation_id.lower():
        raise ValueError('Invalid core operation receipt identifier')
    billing = answers.get('billing')
    if (not isinstance(billing, dict) or not isinstance(billing.get('currency'), str)
            or not re.fullmatch(r'[A-Z]{3}', billing['currency'])):
        raise ValueError('Missing the approved charge basis')
    if set(billing) != {'currency', 'actual', 'approved_max'}:
        raise ValueError('Unexpected fields in the redacted charge basis')
    try:
        actual, ceiling = (Decimal(str(billing.get(field))) for field in ('actual', 'approved_max'))
        if not actual.is_finite() or not ceiling.is_finite() or not 0 <= actual <= ceiling:
            raise ValueError('The session exceeded its approved cost')
    except InvalidOperation as error:
        raise ValueError('Missing the actual session cost or its approved limit') from error
    if observation.get('modality') == 'voice' or 'voice' in answers:
        voice = answers.get('voice')
        if (not isinstance(voice, dict) or set(voice) != set(case['voice']) | {'first_audio_ms'}
                or any(voice.get(k) != v for k, v in case['voice'].items())):
            raise ValueError('The interruption, microphone stop or text continuation failed')
        delay = voice.get('first_audio_ms')
        if type(delay) not in (float, int) or not isfinite(delay) or delay < 0:
            raise ValueError('Missing the observed first audio timing')
    return case['id']
