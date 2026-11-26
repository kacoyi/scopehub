"""Use the local Herdr Core protocol without starting a core or touching parent news."""

from contextlib import contextmanager
from datetime import date, timedelta
import errno
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import tomllib

if __package__:
    from .herdr_lifecycle import LifecycleHold
    from .herdr_returns import ROLL_OUT_NAME
else:
    from herdr_lifecycle import LifecycleHold
    from herdr_returns import ROLL_OUT_NAME


ADDRESS = re.compile(r't[0-9a-z]+')
RPC_LIMIT = 2 * 1024 * 1024
RPC_TIMEOUT = 5
FD_LIMIT = 1024


def core_socket():
    if override := os.environ.get('HC_SOCKET'):
        return Path(override).absolute()
    config = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'herdr-core/config.toml'
    try:
        with config.open('rb') as stream:
            override = tomllib.load(stream).get('root', {}).get('socket')
    except FileNotFoundError:
        override = None
    except (OSError, ValueError, AttributeError):
        raise LifecycleHold('invalid_core_configuration') from None
    if isinstance(override, str) and override:
        return Path(override).absolute()
    session = (os.environ.get('HC_SESSION') or '').strip() or 'default'
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', session):
        raise LifecycleHold('unsupported_core_session')
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if runtime:
        return Path(runtime) / 'herdr-core' / f'{session}.sock'
    runtime = Path('/run/user') / str(os.getuid())
    if runtime.is_dir():
        return runtime / 'herdr-core' / f'{session}.sock'
    state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    return state / 'herdr-core' / session / f'{session}.sock'


class Core:
    def __init__(self, connection, stream):
        self.connection = connection
        self.stream = stream
        self.sequence = 0

    def call(self, method, **params):
        self.sequence += 1
        request = {'jsonrpc': '2.0', 'id': self.sequence, 'method': method, 'params': params}
        self.connection.sendall(json.dumps(request).encode('utf-8') + b'\n')
        reply = self.read()
        if reply.get('id') != self.sequence or reply.get('jsonrpc') != '2.0':
            raise LifecycleHold('invalid_core_reply')
        result = reply.get('result')
        if isinstance(result, dict):
            return result
        error = reply.get('error')
        detail = error.get('data') if isinstance(error, dict) else None
        if isinstance(detail, dict) and detail.get('kind') == 'not_found':
            return None
        raise LifecycleHold('core_operation_failed')

    def read(self):
        raw = self.stream.readline(RPC_LIMIT + 1)
        if not raw.endswith(b'\n') or len(raw) > RPC_LIMIT:
            raise LifecycleHold('invalid_core_reply')
        try:
            result = json.loads(raw)
        except ValueError:
            raise LifecycleHold('invalid_core_reply') from None
        if not isinstance(result, dict):
            raise LifecycleHold('invalid_core_reply')
        return result


@contextmanager
def connect(path, expected=None):
    family = getattr(socket, 'AF_UNIX', None)
    if family is None:
        raise LifecycleHold('unsupported_core_transport')
    try:
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise LifecycleHold('unsafe_core_socket')
        try:
            connection = socket.socket(family)
        except OSError as error:
            if error.errno in (errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT):
                raise LifecycleHold('unsupported_core_transport') from None
            raise
        with connection:
            connection.settimeout(RPC_TIMEOUT)
            connection.connect(str(path))
            with connection.makefile('rb') as stream:
                core = Core(connection, stream)
                hello = core.call('hello')
                if not hello or hello.get('generation') != 1 or 'events' not in (hello.get('features') or ()):
                    raise LifecycleHold('unsupported_core_protocol')
                info = hello.get('core')
                identity = info.get('id') if isinstance(info, dict) else None
                if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity):
                    raise LifecycleHold('invalid_core_reply')
                if expected is not None and identity != expected:
                    raise LifecycleHold('wrong_core')
                yield core, identity
    except OSError:
        raise LifecycleHold('core_unavailable') from None


def _one_rollout(pid, sessions):
    directory = Path('/proc') / str(pid) / 'fd'
    matches = set()
    try:
        with os.scandir(directory) as handles:
            for number, handle in enumerate(handles):
                if number >= FD_LIMIT:
                    raise LifecycleHold('terminal_descriptor_limit')
                try:
                    path = Path(os.readlink(handle.path))
                except FileNotFoundError:
                    continue
                match = ROLL_OUT_NAME.fullmatch(path.name)
                if match and path.is_absolute() and path.is_relative_to(sessions):
                    matches.add((str(path), match[1]))
    except OSError:
        raise LifecycleHold('terminal_process_unavailable') from None
    if len(matches) > 1:
        raise LifecycleHold('original_ambiguous')
    if not matches:
        raise LifecycleHold('original_missing')
    return matches.pop()


def exec_rollout(pid, sessions, session):
    if not isinstance(session, str) or not re.fullmatch(
            r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', session):
        raise LifecycleHold('session_mismatch')
    try:
        path, observed = _one_rollout(pid, sessions)
    except LifecycleHold as error:
        if str(error) not in ('original_missing', 'terminal_process_unavailable'):
            raise
    else:
        if observed != session:
            raise LifecycleHold('session_mismatch')
        return path
    # A short exec can close its descriptor before the wrapper receives thread.started.
    today = date.today()
    paths = [path for offset in (-1, 0, 1)
             for path in (sessions / (today + timedelta(days=offset)).strftime('%Y/%m/%d')).glob(
                 f'rollout-*-{session}.jsonl') if path.resolve() == path]
    if len(paths) != 1:
        raise LifecycleHold('original_ambiguous' if paths else 'original_missing')
    return str(paths[0])


def _check_registration(binding, row):
    # Only inspect the exact launch owned by this registration.
    expected = binding['labels']
    argv = row.get('argv')
    labels = row.get('labels')
    if isinstance(labels, dict):
        # Codex/Herdr can update the native display-only working-directory label.
        labels = {key: value for key, value in labels.items() if key != 'where'}
    if (not isinstance(argv, list) or len(argv) < 3 or not isinstance(argv[-1], str)
            or argv[:-1] != binding.get('launch_argv', binding['codex_argv'])
            or sha256(argv[-1].encode()).hexdigest() != binding['prompt_sha256']
            or labels != expected or row.get('parent') != binding['parent']
            or row.get('cwd') != binding['checkout'] or row.get('name') != binding['name']
            or not ADDRESS.fullmatch(str(row.get('address'))) or type(row.get('created_ms')) is not int):
        raise LifecycleHold('foreign_terminal')
    return argv


def _desktop_spaces(binding):
    try:
        result = subprocess.run(['herdr-app', 'ls'], cwd=binding['checkout'], capture_output=True,
                                timeout=RPC_TIMEOUT, check=True)
        layout = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, RecursionError):
        raise LifecycleHold('desktop_unavailable') from None
    try:
        spaces = layout['spaces']
        if not isinstance(spaces, list) or any(not isinstance(space['tabs'], list) for space in spaces):
            raise TypeError
        if any(not isinstance(tab['terminals'], list) or any(
                not isinstance(terminal, dict) for terminal in tab['terminals'])
                for space in spaces for tab in space['tabs']):
            raise TypeError
    except (KeyError, TypeError):
        raise LifecycleHold('desktop_snapshot_incomplete') from None
    return spaces


def _desktop_id(value):
    return isinstance(value, str) and bool(value.strip())


def _desktop_tab_closed(binding, tab, address):
    spaces = _desktop_spaces(binding)
    # A tab or pane can move to another space, so absence requires complete identities throughout.
    if any(not _desktop_id(space.get('id')) or 'machine' not in space for space in spaces):
        raise LifecycleHold('desktop_snapshot_incomplete')
    tabs = [current for space in spaces for current in space['tabs']]
    if any(not _desktop_id(current.get('id')) or any(
            not _desktop_id(entry.get('pane')) or not _desktop_id(entry.get('terminal'))
            for entry in current['terminals']) for current in tabs):
        raise LifecycleHold('desktop_snapshot_incomplete')
    original_space = any(space['id'] == binding['space'] and space['machine'] is None for space in spaces)
    still_present = any(current['id'] == tab['id'] or any(
        entry['terminal'] == address or entry['pane'] == tab['pane'] for entry in current['terminals'])
        for current in tabs)
    return original_space and not still_present


def _visible_tab(binding, address):
    if binding.get('space') is None:
        return None
    matches = [(space, tab, terminal) for space in _desktop_spaces(binding) for tab in space['tabs']
               for terminal in tab['terminals']
               if terminal.get('terminal') == address and terminal.get('name') == binding['name']]
    if len(matches) != 1:
        raise LifecycleHold('terminal_tab_unavailable')
    space, tab, terminal = matches[0]
    if (space.get('id') != binding['space'] or space.get('machine') is not None
            or len(tab['terminals']) != 1 or not _desktop_id(tab.get('id'))
            or not _desktop_id(terminal.get('pane'))):
        raise LifecycleHold('terminal_tab_changed')
    return {'id': tab['id'], 'pane': terminal['pane']}


class NativeLifecycle:
    """Bind private original discovery to an exact owned terminal."""

    def __init__(self, path, *, execution=None):
        self.path = path
        self.execution = execution

    def own_process(self, binding, address, pid):
        with connect(self.path, binding['core_id']) as (core, _):
            row = core.call('terminal.get', terminal=address)
            if (not row or row.get('address') != address or row.get('state') != 'live'
                    or row.get('pid') != pid):
                raise LifecycleHold('one_shot_owner_unverified')
            _check_registration(binding, row)
        try:
            tab = _visible_tab(binding, address)
        except LifecycleHold:
            tab = None
        return {'address': address, 'created_ms': row['created_ms'], 'pid': pid, 'tab': tab}

    def _observe_one_shot(self, binding, row, expected):
        execution = self.execution(binding['id'])
        if (execution['address'] != row['address'] or execution['created_ms'] != row['created_ms']
                or expected is not None and execution != expected):
            raise LifecycleHold('assignment_execution_changed')
        if row.get('pid') != execution['pid']:
            raise LifecycleHold('terminal_program_changed')
        if row.get('state') == 'live':
            if expected is not None:
                raise LifecycleHold('one_shot_awaiting_exit')
            return execution
        if row.get('state') != 'finished':
            raise LifecycleHold('terminal_program_changed')
        if expected is None:
            # The persisted thread identity can recover its result after an interrupted runner.
            return execution
        exit_code = row.get('exit_code')
        if row.get('reason') != 'exited' or type(exit_code) is not int or exit_code != 0:
            raise LifecycleHold('one_shot_exit_unverified')
        tab = execution['tab']
        if tab is not None:
            if not isinstance(tab, dict) or not _desktop_id(tab.get('id')) or not _desktop_id(tab.get('pane')):
                raise LifecycleHold('terminal_tab_changed')
            if _desktop_tab_closed(binding, tab, row['address']):
                raise LifecycleHold('one_shot_tab_closed')
        raise LifecycleHold('one_shot_exited')

    def _observe(self, core, binding, expected):
        row = core.call('terminal.get', terminal=expected['address'] if expected else binding['name'])
        if row is None:
            return None
        argv = _check_registration(binding, row)
        if binding.get('mode') == 'one-shot':
            return self._observe_one_shot(binding, row, expected)
        identity = {'address': row['address'], 'created_ms': row['created_ms']}
        if expected and any(identity[key] != expected[key] for key in identity):
            raise LifecycleHold('foreign_terminal')
        turn = row.get('turn')
        if expected and type(turn) is int and turn != expected['turn']:
            raise LifecycleHold('terminal_turn_changed')
        if row.get('state') == 'finished':
            # A finished reusable row cannot establish which Codex process and rollout ended.
            raise LifecycleHold('terminal_finished_turn_unverified' if expected else 'terminal_finished_without_archive')
        status = row.get('status') or {}
        if not isinstance(status, dict):
            raise LifecycleHold('invalid_core_reply')
        if row.get('state') != 'live' or status.get('state') not in ('idle', 'done'):
            raise LifecycleHold('terminal_not_ready')
        status_ms = row.get('status_ms')
        if row.get('reported') is not True or type(turn) is not int or turn != 1 or type(status_ms) is not int:
            raise LifecycleHold('terminal_turn_unconfirmed' if not turn else 'terminal_turn_changed')
        foreground = row.get('foreground') or {}
        if not isinstance(foreground, dict):
            raise LifecycleHold('invalid_core_reply')
        pid, actual = foreground.get('pid'), foreground.get('argv')
        if (type(pid) is not int or pid <= 0 or pid != row.get('pid') or not isinstance(actual, list)
                or not actual or not isinstance(actual[0], str) or Path(actual[0]).name != 'codex'
                or actual[1:] != argv[1:] or foreground.get('cwd') != binding['checkout']):
            raise LifecycleHold('terminal_program_changed')
        path, session = _one_rollout(pid, Path(binding['sessions']))
        child_snapshot = core.call('events.subscribe', children_of=row['address'])
        if not child_snapshot or not isinstance(child_snapshot.get('terminals'), list):
            raise LifecycleHold('terminal_children_unverified')
        if any(not isinstance(child, dict) or child.get('state') != 'finished' for child in child_snapshot['terminals']):
            raise LifecycleHold('terminal_has_live_children')
        return {**identity, 'turn': turn, 'pid': pid, 'rollout_path': path,
                'session_id': session, 'tab': _visible_tab(binding, row['address'])}

    def observe(self, binding, *, retiring):
        with connect(self.path, binding['core_id']) as (core, _):
            return self._observe(core, binding, retiring)

    def wait_for_change(self, assignment):
        # Scope to the unique launch marker so this works before startup has returned an address.
        try:
            with connect(self.path) as (core, _):
                core.call('events.subscribe', labels={'scopehub.assignment': assignment})
                core.connection.settimeout(10)
                core.read()
        except LifecycleHold:
            return
