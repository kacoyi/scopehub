"""Use the local Herdr Core protocol without starting a core or touching parent news."""

from contextlib import contextmanager
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
    try:
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise LifecycleHold('unsafe_core_socket')
        with socket.socket(socket.AF_UNIX) as connection:
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


def _check_registration(binding, row):
    # Only inspect the exact direct Codex launch owned by this registration.
    expected = binding['labels']
    argv = row.get('argv')
    if (not isinstance(argv, list) or len(argv) < 3 or not isinstance(argv[-1], str)
            or argv[:-1] != binding['codex_argv'] or sha256(argv[-1].encode()).hexdigest() != binding['prompt_sha256']
            or row.get('labels') != expected or row.get('parent') != binding['parent']
            or row.get('cwd') != binding['checkout'] or row.get('name') != binding['name']
            or not ADDRESS.fullmatch(str(row.get('address'))) or type(row.get('created_ms')) is not int):
        raise LifecycleHold('foreign_terminal')
    return argv


def _desktop_tabs(binding):
    try:
        result = subprocess.run(['herdr-app', 'ls'], cwd=binding['checkout'], capture_output=True,
                                timeout=RPC_TIMEOUT, check=True)
        layout = json.loads(result.stdout)
        entries = [(space, tab) for space in layout['spaces'] for tab in space['tabs']]
        if any(not isinstance(tab['terminals'], list) or
               any(not isinstance(terminal, dict) for terminal in tab['terminals']) for _, tab in entries):
            raise TypeError
        return entries
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        raise LifecycleHold('desktop_unavailable') from None


def _visible_tab(binding, address):
    if binding.get('space') is None:
        return None
    matches = [(space, tab, terminal) for space, tab in _desktop_tabs(binding) for terminal in tab['terminals']
               if terminal.get('terminal') == address and terminal.get('name') == binding['name']]
    if len(matches) != 1:
        raise LifecycleHold('terminal_tab_unavailable')
    space, tab, terminal = matches[0]
    if (space.get('id') != binding['space'] or space.get('machine') is not None
            or len(tab['terminals']) != 1 or not isinstance(tab.get('id'), str)
            or not isinstance(terminal.get('pane'), str)):
        raise LifecycleHold('terminal_tab_changed')
    return {'id': tab['id'], 'pane': terminal['pane']}


def _assert_tab_gone(binding, terminal):
    tab = terminal['tab']
    if tab and any(current.get('id') == tab['id'] for _, current in _desktop_tabs(binding)):
        raise LifecycleHold('desktop_retirement_pending')


class NativeLifecycle:
    """Bind private original discovery and exact retirement to one owned terminal."""

    def __init__(self, path, probe):
        self.path = path
        self.probe = probe

    def _observe(self, core, binding, expected):
        row = core.call('terminal.get', terminal=expected['address'] if expected else binding['name'])
        if row is None:
            if expected:
                _assert_tab_gone(binding, expected)
            return None
        argv = _check_registration(binding, row)
        identity = {'address': row['address'], 'created_ms': row['created_ms']}
        if expected and any(identity[key] != expected[key] for key in identity):
            raise LifecycleHold('foreign_terminal')
        if row.get('state') == 'finished':
            if not expected:
                raise LifecycleHold('terminal_finished_without_archive')
            return {**expected, 'finished': True}
        status = row.get('status') or {}
        if not isinstance(status, dict):
            raise LifecycleHold('invalid_core_reply')
        if row.get('state') != 'live' or status.get('state') not in ('idle', 'done'):
            raise LifecycleHold('terminal_not_ready')
        turn, status_ms = row.get('turn'), row.get('status_ms')
        if row.get('reported') is not True or turn != 1 or type(status_ms) is not int:
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

    def retire(self, binding, terminal, *, original_sha256):
        # Herdr Core has no compare-and-kill API. Recheck the archive proof and the exact generation
        # before using the narrowly scoped terminal operation. The UI reacts to that terminal's exit.
        with connect(self.path, binding['core_id']) as (core, _):
            row = core.call('terminal.get', terminal=terminal['address'])
            if row is None:
                _assert_tab_gone(binding, terminal)
                return
            _check_registration(binding, row)
            if row.get('created_ms') != terminal['created_ms']:
                raise LifecycleHold('foreign_terminal')
            if row.get('state') == 'live':
                if sha256(self.probe(binding, terminal).original).hexdigest() != original_sha256:
                    raise LifecycleHold('original_changed')
                # The scoped child subscription can send notifications after its reply, so observe
                # on its own connection and make the last core read on this command connection.
                if self.observe(binding, retiring=terminal) != terminal:
                    raise LifecycleHold('terminal_changed_before_retirement')
                current = core.call('terminal.get', terminal=terminal['address'])
                if row != current:
                    raise LifecycleHold('terminal_changed_before_retirement')
                core.call('terminal.kill', terminal=terminal['address'])
                row = core.call('terminal.get', terminal=terminal['address'])
            if row is not None:
                _check_registration(binding, row)
                if row.get('state') != 'finished' or row.get('created_ms') != terminal['created_ms']:
                    raise LifecycleHold('terminal_retirement_pending')
                core.call('terminal.rm', terminal=terminal['address'])
                if core.call('terminal.get', terminal=terminal['address']) is not None:
                    raise LifecycleHold('terminal_retirement_pending')
        _assert_tab_gone(binding, terminal)

    def wait_for_change(self, assignment):
        # Scope to the unique launch marker so this works before startup has returned an address.
        try:
            with connect(self.path) as (core, _):
                core.call('events.subscribe', labels={'scopehub.assignment': assignment})
                core.connection.settimeout(10)
                core.read()
        except LifecycleHold:
            return
