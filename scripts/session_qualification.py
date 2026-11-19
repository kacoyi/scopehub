"""Score the redacted results of a real, explicitly authorized synthetic-data session."""
from decimal import Decimal, InvalidOperation
from math import isfinite


def validate_qualification(observation, case):
    """Return the case ID only after the reported fields, effects and limits match."""
    answers = observation.get('qualification')
    if not isinstance(answers, dict) or answers.get('case') != case['id']:
        raise ValueError('Missing the fixed session qualification case')
    if answers.get('confirmed') != case['confirmed']:
        raise ValueError('The confirmed instrument, date, time zone, price or quantity differs')
    digests = set()
    for key in ('initial', 'corrected'):
        measured = answers.get(key)
        if not isinstance(measured, dict) or any(measured.get(k) != v for k, v in case[key].items()):
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
    operation_id = receipt.get('operation_id')
    if not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 256:
        raise ValueError('Missing the observed core operation receipt')
    billing = answers.get('billing')
    if not isinstance(billing, dict) or not isinstance(billing.get('currency'), str) or not billing['currency'].strip():
        raise ValueError('Missing the approved charge basis')
    try:
        actual, ceiling = (Decimal(str(billing.get(field))) for field in ('actual', 'approved_max'))
        if not actual.is_finite() or not ceiling.is_finite() or not 0 <= actual <= ceiling:
            raise ValueError('The session exceeded its approved cost')
    except InvalidOperation as error:
        raise ValueError('Missing the actual session cost or its approved limit') from error
    if observation.get('modality') == 'voice':
        voice = answers.get('voice')
        if not isinstance(voice, dict) or any(voice.get(k) != v for k, v in case['voice'].items()):
            raise ValueError('The interruption, microphone stop or text continuation failed')
        delay = voice.get('first_audio_ms')
        if type(delay) not in (float, int) or not isfinite(delay) or delay < 0:
            raise ValueError('Missing the observed first audio timing')
    return case['id']
