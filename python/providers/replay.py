"""Resolve a finite synthetic replay against an exact frozen observation."""
from typing import Literal

from contracts.generated.protocol import MarketSubscriptionInput, MarketSubscriptionOutput
from providers.history import HistoryError
from providers.history_pages import freeze_sample
from providers.simulated import ReplayObservation


def plan_replay(request: MarketSubscriptionInput) -> tuple[MarketSubscriptionOutput, list[ReplayObservation]]:
    snapshot, after = request['snapshot'], request['after']
    if request['series'] != snapshot['series']:
        raise HistoryError('INVALID_ARGUMENT')
    frozen, position, updates = freeze_sample({
        'series': request['series'], 'from_ms': snapshot['from_ms'], 'to_ms': snapshot['to_ms'],
        'warmup_bars': snapshot['warmup_bars'], 'snapshot': snapshot, 'include_stream_position': True,
    })
    if position == after:
        return {'status': 'accepted', 'from_position': after}, updates

    reason: Literal['coverage_unknown', 'epoch_changed', 'sequence_gap']
    if position is None or position['stream_id'] != after['stream_id']:
        reason = 'coverage_unknown'
    elif position['epoch'] != after['epoch']:
        reason = 'epoch_changed'
    else:
        reason = 'sequence_gap'
    recover_from = frozen.bars[-1]['time_ms'] if position is not None else snapshot['from_ms']
    return {'status': 'backfill_required', 'reason': reason, 'recover_from_ms': recover_from}, []
