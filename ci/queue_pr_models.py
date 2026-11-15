#!/usr/bin/env python3
"""Trusted-main workflow_run dispatcher: PR bytes execute only in Oracle sandbox.

The persistent runner downloads bounded regular Git blobs, invokes the installed
queue client, then uses trusted-main verifier code to inspect returned evidence.
It never imports, runs or checks out PR code on the persistent runner.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

REPOSITORY='sloik/post-ai-worlds'
REPOSITORY_ID=1448630173
SOURCE_WORKFLOW='.github/workflows/checks.yml'
SOURCE_NAME='Public research checks'
DISPATCH_WORKFLOW=REPOSITORY+'/.github/workflows/oracle-pr-tla.yml@refs/heads/main'
CHECK_NAME='validate'
TRUSTED_ROOT=Path(__file__).resolve().parents[1]
SHA=re.compile('[0-9a-f]{40}')
MAX_BLOB=8*1024*1024
MAX_SOURCE=64*1024*1024
MAX_FILES=20000
REQUIRED={'tools/check.py','tools/run_models.py','tools/remote_models.py','tools/campaign.py','worlds/registry.json'}


class Refused(ValueError):pass


def require(value,message):
    if not value:raise Refused(message)


def integer(value):
    return type(value) is int and value>0


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise Refused('GitHub API redirect refused')


class API:
    def __init__(self,token):
        require(bool(token),'GitHub token required for trusted check publication')
        self.token=token
        self.opener=urllib.request.build_opener(NoRedirect)

    def call(self,path,method='GET',body=None):
        require(path.startswith('/repos/'+REPOSITORY+'/'),'API path outside owning repository')
        request=urllib.request.Request('https://api.github.com'+path,
            data=None if body is None else json.dumps(body).encode(),method=method,
            headers={'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json',
                     'X-GitHub-Api-Version':'2022-11-28','User-Agent':'post-ai-oracle-dispatcher'})
        with self.opener.open(request,timeout=60) as response:
            raw=response.read(16*1024*1024+1)
        require(len(raw)<=16*1024*1024,'GitHub API response exceeds cap')
        return json.loads(raw)


def validate_event(event,env):
    require(env.get('GITHUB_SERVER_URL')=='https://github.com','wrong GitHub server')
    require(env.get('GITHUB_REPOSITORY')==REPOSITORY,'wrong repository context')
    require(env.get('GITHUB_EVENT_NAME')=='workflow_run','workflow_run required')
    require(env.get('GITHUB_REF')=='refs/heads/main','dispatcher must be maintained main')
    require(env.get('GITHUB_WORKFLOW_REF')==DISPATCH_WORKFLOW,'wrong trusted dispatcher workflow')
    require(event.get('action')=='completed','upstream run must complete first')
    repo=event.get('repository',{})
    require(type(repo.get('id')) is int and repo['id']==REPOSITORY_ID and repo.get('full_name')==REPOSITORY and repo.get('fork') is False,'wrong repository identity')
    run=event.get('workflow_run',{})
    require(integer(run.get('id')),'invalid source run ID')
    require(run.get('name')==SOURCE_NAME and run.get('event')=='pull_request','not an approved PR source workflow')
    require(run.get('status')=='completed','upstream run incomplete')
    require(isinstance(run.get('head_sha'),str) and SHA.fullmatch(run['head_sha']),'invalid exact source SHA')
    return run


def source_run(api,event_run):
    run=api.call('/repos/'+REPOSITORY+'/actions/runs/'+str(event_run['id']))
    for name in ('id','head_sha','event','status','conclusion','workflow_id'):
        require(run.get(name)==event_run.get(name),'event/API source-run mismatch: '+name)
    require(run.get('name')==SOURCE_NAME and run.get('event')=='pull_request' and run.get('status')=='completed','wrong source run')
    repo=run.get('repository',{})
    require(type(repo.get('id')) is int and repo['id']==REPOSITORY_ID and repo.get('full_name')==REPOSITORY,'source run belongs to another repository')
    require(integer(run.get('workflow_id')),'invalid source workflow ID')
    workflow=api.call('/repos/'+REPOSITORY+'/actions/workflows/'+str(run['workflow_id']))
    require(workflow.get('path')==SOURCE_WORKFLOW,'source workflow path mismatch')
    return run


def current_prs(api,run):
    prs=api.call('/repos/'+REPOSITORY+'/commits/'+run['head_sha']+'/pulls?per_page=100')
    require(isinstance(prs,list) and len(prs)<100,'PR association unavailable/truncated')
    found=[]
    for pr in prs:
        head=pr.get('head',{});base=pr.get('base',{}).get('repo',{})
        if (pr.get('state')=='open' and head.get('sha')==run['head_sha']
                and base.get('id')==REPOSITORY_ID and base.get('full_name')==REPOSITORY):
            require(integer(pr.get('number')),'invalid associated PR number')
            found.append(pr)
    require(found,'no open PR at the exact approved source head')
    return found


def source_path(path):
    require(isinstance(path,str) and 0<len(path)<=1024 and '\\' not in path and not any(ord(x)<32 for x in path),'invalid Git path')
    p=PurePosixPath(path)
    require(not p.is_absolute() and all(x not in ('','.','..') for x in path.split('/')),'Git path escape')
    return path


def snapshot(api,head,destination):
    require(isinstance(head,str) and SHA.fullmatch(head),'invalid snapshot head')
    commit=api.call('/repos/'+REPOSITORY+'/git/commits/'+head)
    require(commit.get('sha')==head,'commit identity mismatch')
    tree_sha=commit.get('tree',{}).get('sha')
    require(isinstance(tree_sha,str) and SHA.fullmatch(tree_sha),'invalid tree identity')
    tree=api.call('/repos/'+REPOSITORY+'/git/trees/'+tree_sha+'?recursive=1')
    require(tree.get('sha')==tree_sha and tree.get('truncated') is False,'tree identity/truncation failure')
    selected={};total=0
    for entry in tree.get('tree',[]):
        path=source_path(entry.get('path'))
        if not path.startswith(('tools/','formal/','worlds/')) or path.startswith('formal/evidence/'):
            continue
        if entry.get('type')=='tree':continue
        require(entry.get('type')=='blob' and entry.get('mode') in ('100644','100755'),'non-regular PR source refused: '+path)
        require(path not in selected,'duplicate PR source')
        require(type(entry.get('size')) is int and 0<=entry['size']<=MAX_BLOB,'PR source size exceeds cap')
        require(isinstance(entry.get('sha'),str) and SHA.fullmatch(entry['sha']),'invalid blob identity')
        total+=entry['size'];require(total<=MAX_SOURCE and len(selected)<MAX_FILES,'PR source closure exceeds cap')
        selected[path]=entry
    require(REQUIRED<=set(selected),'missing native producer inputs')
    require(not any('/'.join(p.split('/')[:i]) in selected for p in selected for i in range(1,len(p.split('/')))),'conflicting source paths')
    destination.mkdir(mode=0o700)
    for path,entry in sorted(selected.items()):
        blob=api.call('/repos/'+REPOSITORY+'/git/blobs/'+entry['sha'])
        require(blob.get('encoding')=='base64' and blob.get('sha')==entry['sha'],'blob envelope mismatch')
        try:data=base64.b64decode(''.join(blob['content'].split()),validate=True)
        except (ValueError,TypeError,KeyError) as exc:raise Refused('invalid blob encoding') from exc
        require(len(data)==entry['size'],'blob size mismatch')
        digest=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        require(digest==entry['sha'],'blob content hash mismatch')
        target=destination/path;target.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o755 if entry['mode']=='100755' else 0o644)
        with os.fdopen(fd,'wb') as f:f.write(data)
    return selected


def trusted_modules():
    # Only maintained-main modules from this checkout are imported. PR paths are
    # never cwd/sys.path/module names, including during evidence verification.
    tools=TRUSTED_ROOT/'tools'
    sys.path.insert(0,str(tools))
    import check
    import campaign
    import remote_models
    for module in (check,campaign,remote_models):
        require(Path(module.__file__).resolve().parent==tools.resolve(),'untrusted verifier import refused')
    return check,campaign,remote_models


def verify_returned(snapshot_root,fetched):
    checker,campaign,adapter=trusted_modules()
    adapter.merge_evidence(fetched,snapshot_root,campaign.RUNS)
    checker.check_registry(checker.load(snapshot_root/'worlds/registry.json'))
    for run_id, _, _, _ in campaign.RUNS:
        text=(snapshot_root/'formal/evidence'/(run_id+'.log')).read_text()
        require('/Users/' not in text and '/home/' not in text,'private native path in returned capture')
    manifest=checker.load(snapshot_root/'formal/evidence/manifest.json')
    java=Path('/usr/bin/java').resolve()
    require(manifest.get('java_executable')==str(java),'returned JDK executable identity mismatch')
    require(manifest.get('java_sha256')==checker.digest(java),'returned JDK binary identity mismatch')
    return checker.verify_evidence(snapshot_root)


def publish(api,head,run_id,status,conclusion=None,summary=''):
    body={'name':CHECK_NAME,'head_sha':head,'status':status,
          'external_id':'oracle-pr-source-run-'+str(run_id),
          'output':{'title':'Static checks and Oracle finite models','summary':summary[:60000]}}
    if conclusion:
        body.update(conclusion=conclusion,completed_at=datetime.now(timezone.utc).isoformat())
    return api.call('/repos/'+REPOSITORY+'/check-runs','POST',body)


def update(api,check_id,conclusion,summary):
    require(integer(check_id),'invalid check-run ID')
    return api.call('/repos/'+REPOSITORY+'/check-runs/'+str(check_id),'PATCH',
        {'status':'completed','conclusion':conclusion,'completed_at':datetime.now(timezone.utc).isoformat(),
         'output':{'title':'Static checks and Oracle finite models','summary':summary[:60000]}})


def dispatch(api,event,env):
    run=source_run(api,validate_event(event,env))
    head=run['head_sha']
    if run.get('conclusion')!='success':
        publish(api,head,run['id'],'completed','failure','Upstream public source/static checks did not succeed; no native job submitted.')
        return 1
    try:
        prs=current_prs(api,run)
    except Refused:
        publish(api,head,run['id'],'completed','failure','No open PR remains at the exact approved source head; no native job submitted.')
        return 1
    check_run=publish(api,head,run['id'],'in_progress',summary='Approved hosted PR checks succeeded. Native campaign is queued for the exact head '+head+'.')
    require(integer(check_run.get('id')),'missing created check-run ID')
    try:
        client=shutil.which('tla-queue');require(client is not None,'Oracle queue client unavailable; no local fallback')
        with tempfile.TemporaryDirectory(prefix='post-ai-pr-') as temporary:
            source=Path(temporary)/'source';fetched=Path(temporary)/'fetched'
            snapshot(api,head,source)
            command=[client,'run','--project',REPOSITORY,'--source',str(source),
                     '--include','tools','--include','formal','--include','worlds',
                     '--request-key','post-ai-pr-'+str(run['id'])+'-'+head,
                     '--timeout','1800','--output','formal/evidence','--output','formal/RegistrySnapshot.tla',
                     '--fetch',str(fetched),'--','/usr/bin/python3','-B','tools/run_models.py',
                     '--root','.','--java','/usr/bin/java','--jar','/opt/tla/tla2tools-1.7.4.jar']
            result=subprocess.run(command,cwd=TRUSTED_ROOT)
            require(result.returncode==0,'Oracle command did not complete successfully')
            count=verify_returned(source,fetched)
            require(count==12,'trusted current campaign must qualify all twelve cases')
            # Head movement cannot be credited by a delayed successful job.
            for pr in prs:
                current=api.call('/repos/'+REPOSITORY+'/pulls/'+str(pr['number']))
                require(current.get('state')=='open' and current.get('head',{}).get('sha')==head,'PR head changed/closed while native job ran')
        update(api,check_run['id'],'success','Upstream static checks and trusted verification of all twelve actual Oracle captures passed for exact head '+head+'. Native output/source/tool bindings verified without importing PR code on the persistent runner.')
        return 0
    except Exception as exc:
        # Do not quote arbitrary payload stdout/stderr into a privileged API write.
        message=type(exc).__name__+': '+str(exc)
        update(api,check_run['id'],'failure','Oracle verification failed for exact head '+head+'. '+message[:4000])
        print(message,file=sys.stderr)
        return 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--event',type=Path,default=os.environ.get('GITHUB_EVENT_PATH'))
    args=parser.parse_args()
    require(args.event is not None,'workflow_run event file required')
    event=json.loads(args.event.read_text())
    return dispatch(API(os.environ.get('GITHUB_TOKEN')),event,os.environ)


if __name__=='__main__':
    try:raise SystemExit(main())
    except (Refused,OSError,ValueError,urllib.error.URLError) as exc:
        print(type(exc).__name__+': '+str(exc),file=sys.stderr);raise SystemExit(1)
