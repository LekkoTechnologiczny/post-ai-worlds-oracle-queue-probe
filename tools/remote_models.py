"""Submit the complete immutable Post-AI Worlds native producer to Oracle."""
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

PROJECT = 'sloik/post-ai-worlds'


def is_worker():
    marker = os.environ.get('TLA_QUEUE_JOB')
    if not marker:
        return False
    if (not re.fullmatch('[0-9a-f]{32}', marker)
            or sys.platform != 'linux'
            or pwd.getpwuid(os.getuid()).pw_name != 'tla-job'):
        raise RuntimeError('invalid Oracle queue worker context; no local TLC execution')
    cgroup = Path('/proc/self/cgroup').read_text()
    if not re.search(r'(?:^|/)tla-q-' + re.escape(marker) + r'\.service(?:/|$)', cgroup, re.MULTILINE):
        raise RuntimeError('Oracle queue job cgroup mismatch; no local TLC execution')
    return True


def _read_owned_logs(source, names, require_complete):
    """Fetch was restored by the queue client; still reject links and unknown names."""
    values = {}
    if source.is_symlink():
        raise RuntimeError('fetched output directory must not be a symlink')
    if not source.exists():
        if require_complete:
            raise RuntimeError('queue completed without fetched native logs')
        return values
    root_fd = os.open(str(source), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in names:
            try:
                st = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                if require_complete:
                    raise RuntimeError('missing native log: ' + name)
                continue
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise RuntimeError('native log must be a regular unlinked file: ' + name)
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
            with os.fdopen(fd, 'rb') as stream:
                values[name] = stream.read()
    finally:
        os.close(root_fd)
    return values


def _merge_at(directory, values):
    for name in values:
        try:
            st = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise RuntimeError('output target must be regular and unlinked: ' + name)
    for name, data in values.items():
        temporary = '.post-ai-log-' + uuid.uuid4().hex
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
    os.fsync(directory)


def merge_evidence(fetched, root, runs):
    # Read and validate the entire returned closure before changing local files.
    evidence = _read_owned_logs(fetched / 'formal/evidence',
                               [run_id + '.log' for run_id, _, _, _ in runs] + ['manifest.json'], True)
    snapshot = _read_owned_logs(fetched / 'formal', ['RegistrySnapshot.tla'], True)
    root_fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        formal_fd = os.open('formal', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            try:
                os.mkdir('evidence', 0o755, dir_fd=formal_fd)
            except FileExistsError:
                pass
            evidence_fd = os.open('evidence', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=formal_fd)
            try:
                _merge_at(evidence_fd, evidence)
            finally:
                os.close(evidence_fd)
            _merge_at(formal_fd, snapshot)
        finally:
            os.close(formal_fd)
    finally:
        os.close(root_fd)


def source_paths(runs, dependencies):
    paths = {'tools/check.py', 'tools/run_models.py', 'tools/remote_models.py',
             'tools/campaign.py', 'worlds/registry.json'}
    for run_id, model, _, _ in runs:
        paths.update('formal/' + name for name in dependencies[model])
        paths.add('formal/' + run_id + '.cfg')
    return sorted(paths)


def snapshot_inputs(root, destination, paths):
    root_fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for path in paths:
            parent, name = path.split('/')
            directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                with os.fdopen(fd, 'rb') as original:
                    st = os.fstat(original.fileno())
                    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                        raise OSError('producer input must be regular and unlinked: ' + path)
                    copied = destination / path
                    copied.parent.mkdir(parents=True, exist_ok=True)
                    with copied.open('xb') as output:
                        shutil.copyfileobj(original, output)
            finally:
                os.close(directory)
    finally:
        os.close(root_fd)


def run(args, root, runs, dependencies):
    client = shutil.which('tla-queue')
    if client is None:
        print('FAIL: Oracle queue client tla-queue unavailable; no local TLC fallback', file=sys.stderr)
        return 78
    with tempfile.TemporaryDirectory(prefix='post-ai-queue-') as temporary:
        source = Path(temporary) / 'source'
        fetched = Path(temporary) / 'fetched'
        try:
            snapshot_inputs(root, source, source_paths(runs, dependencies))
        except OSError as exc:
            print('FAIL: producer input snapshot: ' + str(exc), file=sys.stderr)
            return 78
        command = [client, 'run', '--project', PROJECT, '--source', str(source),
                   '--include', 'tools', '--include', 'formal', '--include', 'worlds',
                   '--timeout', '1800', '--output', 'formal/evidence',
                   '--output', 'formal/RegistrySnapshot.tla']
        if not args.no_fetch:
            command += ['--fetch', str(fetched)]
        command += ['--', '/usr/bin/python3', '-B', 'tools/run_models.py',
                    '--root', '.', '--java', args.java, '--jar', str(args.jar)]
        try:
            result = subprocess.run(command, cwd=root)
        except OSError as exc:
            print('FAIL: Oracle queue backend unavailable: ' + str(exc) + '; no local TLC fallback', file=sys.stderr)
            return 75
        if result.returncode != 0:
            # Partial failed evidence remains on Oracle; do not overwrite a
            # caller's previous complete campaign with incomplete artifacts.
            return result.returncode
        if not args.no_fetch:
            try:
                merge_evidence(fetched, root, runs)
            except (OSError, RuntimeError) as exc:
                print('FAIL: remote evidence retrieval: ' + str(exc), file=sys.stderr)
                return 1
        return 0
