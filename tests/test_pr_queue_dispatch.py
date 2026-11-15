"""Trusted dispatcher controls: no candidate code is executed on the runner."""
import base64
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('pr_dispatch',ROOT/'ci/queue_pr_models.py')
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
HEAD='a'*40
TREE='b'*40
PREFIX='/repos/'+d.REPOSITORY


def context():
    env={'GITHUB_SERVER_URL':'https://github.com','GITHUB_REPOSITORY':d.REPOSITORY,
         'GITHUB_EVENT_NAME':'workflow_run','GITHUB_REF':'refs/heads/main',
         'GITHUB_WORKFLOW_REF':d.DISPATCH_WORKFLOW}
    run={'id':99,'name':d.SOURCE_NAME,'event':'pull_request','status':'completed',
         'conclusion':'success','workflow_id':100,'head_sha':HEAD,
         'repository':{'id':d.REPOSITORY_ID,'full_name':d.REPOSITORY}}
    event={'action':'completed','repository':{'id':d.REPOSITORY_ID,'full_name':d.REPOSITORY,'fork':False},'workflow_run':run}
    return event,env


def pr():
    return {'number':1,'state':'open','head':{'sha':HEAD},'base':{'repo':{'id':d.REPOSITORY_ID,'full_name':d.REPOSITORY}}}


class FakeAPI:
    def __init__(self):
        self.run=context()[0]['workflow_run'];self.pull=pr();self.writes=[]
    def call(self,path,method='GET',body=None):
        if method!='GET':
            self.writes.append((path,method,copy.deepcopy(body)));return {'id':101}
        routes={PREFIX+'/actions/runs/99':self.run,PREFIX+'/actions/workflows/100':{'path':d.SOURCE_WORKFLOW},
                PREFIX+'/commits/'+HEAD+'/pulls?per_page=100':[pr()],PREFIX+'/pulls/1':self.pull}
        return copy.deepcopy(routes[path])


class EventTests(unittest.TestCase):
    def test_only_exact_default_branch_dispatcher_and_completed_pr_source(self):
        event,env=context();self.assertEqual(99,d.validate_event(event,env)['id'])
        for key,value in [('GITHUB_REF','refs/heads/other'),('GITHUB_EVENT_NAME','pull_request_target'),('GITHUB_WORKFLOW_REF','other'),('GITHUB_REPOSITORY','foreign/repo')]:
            changed=dict(env);changed[key]=value
            with self.subTest(key=key),self.assertRaises(d.Refused):d.validate_event(event,changed)
        for key,value in [('id',True),('event','push'),('status','requested'),('head_sha','../escape'),('name','attacker workflow')]:
            changed=copy.deepcopy(event);changed['workflow_run'][key]=value
            with self.subTest(key=key),self.assertRaises(d.Refused):d.validate_event(changed,env)

    def test_authoritative_run_and_workflow_path_must_match(self):
        event,_=context();api=FakeAPI()
        self.assertEqual(99,d.source_run(api,event['workflow_run'])['id'])
        api.run['head_sha']='c'*40
        with self.assertRaises(d.Refused):d.source_run(api,event['workflow_run'])
        api=FakeAPI();real=api.call
        with mock.patch.object(api,'call',side_effect=lambda path,**kw:{'path':'attacker.yml'} if '/actions/workflows/' in path else real(path,**kw)):
            with self.assertRaises(d.Refused):d.source_run(api,event['workflow_run'])

    def test_head_association_must_be_open_and_exact(self):
        api=FakeAPI();self.assertEqual(1,d.current_prs(api,api.run)[0]['number'])
        with mock.patch.object(api,'call',return_value=[dict(pr(),head={'sha':'c'*40})]):
            with self.assertRaises(d.Refused):d.current_prs(api,api.run)


class SnapshotTests(unittest.TestCase):
    def fixture(self):
        files={name:b'source fixture' for name in d.REQUIRED}
        files['formal/Example.tla']=b'---- MODULE Example ----\n===='
        entries=[];blobs={}
        for path,data in files.items():
            sha=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
            entries.append({'path':path,'type':'blob','mode':'100644','size':len(data),'sha':sha})
            blobs[sha]={'sha':sha,'encoding':'base64','content':base64.b64encode(data).decode()}
        tree={'sha':TREE,'truncated':False,'tree':entries}
        def call(path):
            if '/git/commits/' in path:return {'sha':HEAD,'tree':{'sha':TREE}}
            if '/git/trees/' in path:return copy.deepcopy(tree)
            return copy.deepcopy(blobs[path.rsplit('/',1)[1]])
        return tree,blobs,type('API',(),{'call':staticmethod(call)})()

    def test_snapshot_materializes_verified_regular_blobs_without_exec(self):
        tree,blobs,api=self.fixture()
        with tempfile.TemporaryDirectory() as temp,mock.patch.object(d.subprocess,'run',side_effect=AssertionError('no candidate exec')):
            target=Path(temp)/'snapshot';selected=d.snapshot(api,HEAD,target)
            self.assertEqual({x['path'] for x in tree['tree']},set(selected))
            self.assertEqual(b'source fixture',(target/'tools/check.py').read_bytes())
            self.assertFalse((target/'.git').exists())

    def test_link_submodule_path_duplicate_and_truncated_tree_refused(self):
        for mutation in ('symlink','submodule','path','duplicate','truncated','size'):
            tree,blobs,api=self.fixture()
            if mutation=='symlink':tree['tree'][0]['mode']='120000'
            if mutation=='submodule':tree['tree'][0].update(type='commit',mode='160000')
            if mutation=='path':tree['tree'][0]['path']='tools/../escape'
            if mutation=='duplicate':tree['tree'].append(copy.deepcopy(tree['tree'][0]))
            if mutation=='truncated':tree['truncated']=True
            if mutation=='size':tree['tree'][0]['size']=d.MAX_BLOB+1
            with tempfile.TemporaryDirectory() as temp,self.subTest(mutation=mutation),self.assertRaises(d.Refused):d.snapshot(api,HEAD,Path(temp)/'snapshot')

    def test_git_blob_hash_and_size_are_verified(self):
        tree,blobs,api=self.fixture()
        one=next(iter(blobs.values()));one['content']=base64.b64encode(b'different bytes').decode()
        with tempfile.TemporaryDirectory() as temp,self.assertRaises(d.Refused):d.snapshot(api,HEAD,Path(temp)/'snapshot')

    def test_old_evidence_is_not_submitted_as_fresh(self):
        tree,blobs,api=self.fixture();tree['tree'].append({'path':'formal/evidence/fake.log','type':'blob','mode':'120000'})
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'snapshot';d.snapshot(api,HEAD,target)
            self.assertFalse((target/'formal/evidence').exists())


class TrustedVerifierTests(unittest.TestCase):
    def test_candidate_python_is_data_only_for_trusted_verification(self):
        # Synthetic security control in disposable files, not a new proof receipt:
        # deliberately hostile candidate Python must never be imported on host.
        import shutil
        checker, campaign, adapter = d.trusted_modules()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';source.mkdir()
            for folder in ('tools','formal','worlds'):
                shutil.copytree(ROOT/folder,source/folder,ignore=shutil.ignore_patterns('__pycache__'))
            fetched=root/'fetched';(fetched/'formal').mkdir(parents=True)
            shutil.copytree(ROOT/'formal/evidence',fetched/'formal/evidence')
            shutil.copyfile(ROOT/'formal/RegistrySnapshot.tla',fetched/'formal/RegistrySnapshot.tla')
            candidate=source/'tools/check.py';candidate.write_text('raise AssertionError("candidate code executed on host")')
            manifest=checker.load(fetched/'formal/evidence/manifest.json')
            manifest['producer_inputs']['tools/check.py']=checker.digest(candidate)
            java=Path('/usr/bin/java').resolve()
            manifest['java_executable']=str(java);manifest['java_sha256']=checker.digest(java)
            (fetched/'formal/evidence/manifest.json').write_text(json.dumps(manifest))
            self.assertEqual(12,d.verify_returned(source,fetched))
            self.assertEqual(ROOT/'tools/check.py',Path(checker.__file__))

class VerdictTests(unittest.TestCase):
    def exercise(self,api,native_exit=0,verify=12):
        event,env=context()
        def snapshot(fake_api,head,root):root.mkdir()
        with mock.patch.object(d,'snapshot',side_effect=snapshot),mock.patch.object(d.shutil,'which',return_value='/installed/tla-queue'),mock.patch.object(d.subprocess,'run',return_value=subprocess.CompletedProcess([],native_exit)) as launch,mock.patch.object(d,'verify_returned',return_value=verify) as verifier:
            result=d.dispatch(api,event,env)
        return result,launch,verifier

    def test_combined_success_requires_upstream_native_and_trusted_artifacts(self):
        api=FakeAPI();result,launch,verifier=self.exercise(api)
        self.assertEqual(0,result);self.assertEqual(1,launch.call_count);self.assertEqual(1,verifier.call_count)
        created=api.writes[0][2];completed=api.writes[-1][2]
        self.assertEqual('validate',created['name']);self.assertEqual(HEAD,created['head_sha'])
        self.assertEqual('success',completed['conclusion'])
        command=launch.call_args.args[0]
        self.assertEqual('/installed/tla-queue',command[0]);self.assertNotIn('--env',command)
        self.assertIn('formal/evidence',command);self.assertIn('formal/RegistrySnapshot.tla',command)
        self.assertEqual(d.TRUSTED_ROOT,launch.call_args.kwargs['cwd'])

    def test_unsuccessful_hosted_check_never_submits_native_job(self):
        event,env=context();event['workflow_run']['conclusion']='failure';api=FakeAPI();api.run['conclusion']='failure'
        with mock.patch.object(d.subprocess,'run') as launch:
            self.assertEqual(1,d.dispatch(api,event,env))
        launch.assert_not_called();self.assertEqual('failure',api.writes[-1][2]['conclusion'])

    def test_static_failure_publishes_even_without_open_pr(self):
        event,env=context();event['workflow_run']['conclusion']='cancelled';api=FakeAPI();api.run['conclusion']='cancelled'
        real=api.call
        with mock.patch.object(api,'call',side_effect=lambda path,*args,**kw:[] if '/commits/' in path else real(path,*args,**kw)),mock.patch.object(d.subprocess,'run') as launch:
            self.assertEqual(1,d.dispatch(api,event,env))
        launch.assert_not_called();self.assertEqual('failure',api.writes[-1][2]['conclusion'])

    def test_stale_head_before_submission_publishes_non_success_without_queue(self):
        event,env=context();api=FakeAPI();real=api.call
        with mock.patch.object(api,'call',side_effect=lambda path,*args,**kw:[] if '/commits/' in path else real(path,*args,**kw)),mock.patch.object(d.subprocess,'run') as launch:
            self.assertEqual(1,d.dispatch(api,event,env))
        launch.assert_not_called();self.assertEqual('failure',api.writes[-1][2]['conclusion'])

    def test_successful_exit_without_qualified_artifacts_never_passes(self):
        api=FakeAPI();result,launch,verifier=self.exercise(api,verify=11)
        self.assertEqual(1,result);self.assertEqual('failure',api.writes[-1][2]['conclusion'])

    def test_native_nonzero_never_reaches_artifact_success(self):
        api=FakeAPI();result,launch,verifier=self.exercise(api,native_exit=75)
        self.assertEqual(1,result);verifier.assert_not_called();self.assertEqual('failure',api.writes[-1][2]['conclusion'])

    def test_stale_head_after_native_success_is_not_credited(self):
        api=FakeAPI();api.pull['head']['sha']='c'*40
        result,launch,verifier=self.exercise(api)
        self.assertEqual(1,result);self.assertEqual('failure',api.writes[-1][2]['conclusion'])
        self.assertEqual(HEAD,api.writes[0][2]['head_sha'])


if __name__=='__main__':unittest.main()
