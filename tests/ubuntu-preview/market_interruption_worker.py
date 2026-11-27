"""Run the real synthetic worker with its closing update held for the test."""

import sys
from threading import Event
from uuid import UUID

from providers.simulated import GapObservation, ReplayObservation
from worker_runtime import __main__ as runtime
from worker_runtime.replay import Replay


class InterruptionReplay(Replay):
    def _wait_before_update(self, stop: Event, update: ReplayObservation) -> bool:
        # Publish the genuine partial on the ordinary schedule. The closing
        # update belongs to the replacement. Cancellation or EOF releases
        # this wait through the ordinary replay shutdown.
        if not isinstance(update, GapObservation) and update['bar']['is_closed']:
            stop.wait()
            return True
        return super()._wait_before_update(stop, update)


if __name__ == '__main__':
    UUID(sys.argv[1])
    runtime.Replay = InterruptionReplay
    raise SystemExit(runtime.run('market', demo=True, version='1.1'))
