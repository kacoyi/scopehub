"""Read-only synthetic history; no source fallback, network, files or live feed."""

from contracts.generated.protocol import (
    ErrorCode, InstrumentSearchOutput, MarketGetBarsOutput, Request,
)
from providers.simulated import PROVIDER_ID, TIMEFRAMES, build_fixture, instruments


class HistoryError(Exception):
    def __init__(self, code: ErrorCode) -> None:
        self.code = code


def execute(request: Request) -> InstrumentSearchOutput | MarketGetBarsOutput:
    if request['operation'] == 'instrument.search':
        search = request['payload']
        query = search['query'].strip().casefold()
        return {'instruments': [item for item in instruments()
                                if query in (item['id'] + ' ' + item['name']).casefold()][:search['limit']]}
    if request['operation'] == 'market.getBars':
        params = request['payload']
        if params['provider_id'] != PROVIDER_ID:
            raise HistoryError('PROVIDER_NOT_CONFIGURED')
        if params['from_ms'] >= params['to_ms']:
            raise HistoryError('INVALID_ARGUMENT')
        if params['timeframe'] not in TIMEFRAMES:
            raise HistoryError('UNSUPPORTED_CAPABILITY')
        if params['instrument_id'] not in {item['id'] for item in instruments()}:
            raise HistoryError('DATA_UNAVAILABLE')
        history = build_fixture(params['instrument_id'], params['timeframe'])['history']
        # Half-open UTC start-time range; a partial final bar stays partial.
        history['bars'] = [bar for bar in history['bars'] if params['from_ms'] <= bar['time_ms'] < params['to_ms']]
        return history
    raise HistoryError('UNSUPPORTED_CAPABILITY')
