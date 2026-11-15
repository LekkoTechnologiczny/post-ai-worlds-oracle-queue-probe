"""Oracle transport controls. These tests never execute local TLC."""
import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import remote_models
import run_models
from campaign import RUNS,DEPENDENCIES


class RoutingTests(unittest.TestCase):
    def test_missing_client_does_not_require_local_java_or_jar(self):
        with mock.patch.dict(os.environ,{},clear=True),mock.patch.object(remote_models.shutil,'which',return_value=None),mock.patch.object(remote_models.subprocess,'run') as launch,mock.patch.object(Path,'read_bytes',side_effect=AssertionError('no local JAR read')),contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(78,run_models.main(['--jar','/oracle/only.jar']))
        launch.assert_not_called();self.assertIn('no local TLC fallback',err.getvalue())

    def test_backend_failure_preserved_with_no_native_fallback(self):
        with mock.patch.dict(os.environ,{},clear=True),mock.patch.object(remote_models.shutil,'which',return_value='/client/tla-queue'),mock.patch.object(remote_models.subprocess,'run',return_value=subprocess.CompletedProcess([],75)) as launch:
            self.assertEqual(75,run_models.main(['--java','/oracle/jdk/bin/java','--jar','/oracle/tool.jar']))
        self.assertEqual(1,launch.call_count)
        command=launch.call_args.args[0];split=command.index('--')
        self.assertEqual(['/usr/bin/python3','-B','tools/run_models.py','--root','.','--java','/oracle/jdk/bin/java','--jar','/oracle/tool.jar'],command[split+1:])
        self.assertEqual('sloik/post-ai-worlds',command[command.index('--project')+1])
        self.assertIn('formal/evidence',command[:split]);self.assertIn('formal/RegistrySnapshot.tla',command[:split])

    def test_no_fetch_still_requests_complete_remote_capture(self):
        with mock.patch.dict(os.environ,{},clear=True),mock.patch.object(remote_models.shutil,'which',return_value='/client/tla-queue'),mock.patch.object(remote_models.subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as launch,mock.patch.object(remote_models,'merge_evidence') as merge:
            self.assertEqual(0,run_models.main(['--jar','/oracle/only.jar','--no-fetch']))
        command=launch.call_args.args[0];self.assertNotIn('--fetch',command)
        self.assertEqual(2,command[:command.index('--')].count('--output'))
        merge.assert_not_called()

    def test_backend_os_error_does_not_execute_native(self):
        with mock.patch.dict(os.environ,{},clear=True),mock.patch.object(remote_models.shutil,'which',return_value='/client/tla-queue'),mock.patch.object(remote_models.subprocess,'run',side_effect=OSError('offline')) as launch,contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(75,run_models.main(['--jar','/oracle/only.jar']))
        self.assertEqual(1,launch.call_count);self.assertIn('no local TLC fallback',err.getvalue())

    def test_source_closure_and_default_fourteen_owned_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve()
            for folder in ('tools','formal','worlds'):
                shutil.copytree(ROOT/folder,root/folder,ignore=shutil.ignore_patterns('__pycache__'))
            (root/'formal/evidence/unrelated').write_text('keep')
            def backend(command,**kwargs):
                source=Path(command[command.index('--source')+1])
                self.assertEqual(set(remote_models.source_paths(RUNS,DEPENDENCIES)),{str(p.relative_to(source)) for p in source.rglob('*') if p.is_file()})
                self.assertNotIn('formal/evidence/manifest.json',{str(p.relative_to(source)) for p in source.rglob('*') if p.is_file()})
                fetched=Path(command[command.index('--fetch')+1])
                evidence=fetched/'formal/evidence';evidence.mkdir(parents=True)
                for run_id,_,_,_ in RUNS:(evidence/(run_id+'.log')).write_text('fresh '+run_id)
                (evidence/'manifest.json').write_text('fresh manifest')
                (evidence/'unowned').write_text('must not copy')
                (fetched/'formal/RegistrySnapshot.tla').write_text('fresh snapshot')
                return subprocess.CompletedProcess(command,0)
            with mock.patch.dict(os.environ,{},clear=True),mock.patch.object(remote_models.shutil,'which',return_value='/client/tla-queue'),mock.patch.object(remote_models.subprocess,'run',side_effect=backend):
                self.assertEqual(0,run_models.main(['--root',str(root),'--jar','/oracle/tool.jar']))
            self.assertEqual('keep',(root/'formal/evidence/unrelated').read_text())
            self.assertFalse((root/'formal/evidence/unowned').exists())
            self.assertEqual('fresh snapshot',(root/'formal/RegistrySnapshot.tla').read_text())
            self.assertEqual('fresh manifest',(root/'formal/evidence/manifest.json').read_text())
            for run_id,_,_,_ in RUNS:self.assertEqual('fresh '+run_id,(root/'formal/evidence'/(run_id+'.log')).read_text())

    def test_success_without_output_is_not_accepted(self):
        with mock.patch.dict(os.environ,{},clear=True),mock.patch.object(remote_models.shutil,'which',return_value='/client/tla-queue'),mock.patch.object(remote_models.subprocess,'run',return_value=subprocess.CompletedProcess([],0)),contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(1,run_models.main(['--jar','/oracle/tool.jar']))

    def test_forged_worker_context_denied_before_native_launch(self):
        with mock.patch.dict(os.environ,{'TLA_QUEUE_JOB':'a'*32},clear=True),mock.patch.object(remote_models.sys,'platform','darwin'),mock.patch.object(remote_models.subprocess,'run') as launch,contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:run_models.main(['--jar','/oracle/tool.jar'])
        self.assertEqual(2,raised.exception.code);launch.assert_not_called()

    def test_genuine_worker_requires_uid_and_cgroup(self):
        marker='a'*32
        with mock.patch.dict(os.environ,{'TLA_QUEUE_JOB':marker},clear=True),mock.patch.object(remote_models.sys,'platform','linux'),mock.patch.object(remote_models.pwd,'getpwuid',return_value=type('User',(),{'pw_name':'tla-job'})()),mock.patch.object(Path,'read_text',return_value='0::/system.slice/tla-q-'+marker+'.service\n'):
            self.assertTrue(remote_models.is_worker())
        with mock.patch.dict(os.environ,{'TLA_QUEUE_JOB':marker},clear=True),mock.patch.object(remote_models.sys,'platform','linux'),mock.patch.object(remote_models.pwd,'getpwuid',return_value=type('User',(),{'pw_name':'root'})()):
            with self.assertRaises(RuntimeError):remote_models.is_worker()


class FilesystemTests(unittest.TestCase):
    def make_outputs(self,root):
        evidence=root/'formal/evidence';evidence.mkdir(parents=True)
        for run_id,_,_,_ in RUNS:(evidence/(run_id+'.log')).write_text('native fixture')
        (evidence/'manifest.json').write_text('manifest fixture')
        (root/'formal/RegistrySnapshot.tla').write_text('snapshot fixture')

    def test_output_directory_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();fetched=root/'fetched';self.make_outputs(fetched)
            destination=root/'destination';destination.mkdir();outside=root/'outside';outside.mkdir()
            (destination/'formal').symlink_to(outside,target_is_directory=True)
            with self.assertRaises(OSError):remote_models.merge_evidence(fetched,destination,RUNS)
            self.assertEqual([],list(outside.iterdir()))

    def test_owned_log_symlink_rejected_preserving_private_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();fetched=root/'fetched';self.make_outputs(fetched)
            destination=root/'destination';(destination/'formal/evidence').mkdir(parents=True)
            private=root/'private';private.write_text('keep')
            (destination/'formal/evidence/contracts-safe.log').symlink_to(private)
            with self.assertRaises(RuntimeError):remote_models.merge_evidence(fetched,destination,RUNS)
            self.assertEqual('keep',private.read_text())

    def test_snapshot_refuses_symlinked_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();(root/'tools').mkdir();(root/'tools/check.py').symlink_to('/etc/passwd')
            with self.assertRaises(OSError):remote_models.snapshot_inputs(root,root/'snapshot',['tools/check.py'])
            self.assertFalse((root/'snapshot/tools/check.py').exists())


if __name__=='__main__':unittest.main()
