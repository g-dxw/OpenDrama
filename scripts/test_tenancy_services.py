#!/usr/bin/env python3
"""Provision disposable, isolated PostgreSQL/Redis for JJTV tenancy tests.

create/cleanup/remote-run must run under release.py --target video-test-services.
check is read-only; run executes local tests against only these disposable services.
Credentials stay in a private local temporary JSON file, never in Git or logs.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shlex
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
PURPOSE = "jjtv-tenancy-tests"
TEST_DATABASE = "jjtv_tenancy_test"
POSTGRES_PORT = 15433
REDIS_PORT = 16380
DEFAULT_TEST_IMAGE = "jjtv-api:f6428e9dfaa18148e012eb7bcf5da5c4ab7e50f5"

REMOTE = r'''
import json,re,shutil,subprocess,sys,time
from pathlib import Path
request=json.load(sys.stdin)
action=request['action']
assert action in ['create','check','cleanup','remote-run']
instance=request['instance_id']
assert re.fullmatch(r'[0-9a-f]{32}',instance)
specs=[('jjtv-tenancy-test-postgres','postgres:16-alpine','5432/tcp','15433'),
       ('jjtv-tenancy-test-redis','redis:7-alpine','6379/tcp','16380')]
network='jjtv-tenancy-tests-'+instance[:12]
secrets=[request['postgres_password'],request['redis_password']]
if request.get('connection_key'):secrets.append(request['connection_key'])
def fail(message):
 for value in secrets:message=message.replace(value,'[redacted]')
 raise SystemExit(message)
def command(args):
 p=subprocess.run(args,capture_output=True,text=True,stdin=subprocess.DEVNULL)
 if p.returncode:fail('Test service operation failed: '+p.stderr[-1500:])
 return p.stdout
network_result=subprocess.run(['docker','network','inspect',network],capture_output=True,text=True,stdin=subprocess.DEVNULL)
if network_result.returncode and not re.search(r'not found|no such',network_result.stderr,re.I):
 fail('Could not inspect the owned test network.')
if network_result.returncode==0:
 labels=json.loads(network_result.stdout)[0].get('Labels',{})
 if labels.get('com.88jj.jjtv.purpose')!='jjtv-tenancy-tests' or labels.get('com.88jj.jjtv.instance')!=instance:
  fail('Refusing to touch an unowned network: '+network)
elif action=='create':
 command(['docker','network','create','--label','com.88jj.jjtv.purpose=jjtv-tenancy-tests',
          '--label','com.88jj.jjtv.instance='+instance,network])
runner_name='jjtv-tenancy-test-runner-'+instance[:12]
scratch=Path('/tmp')/('jjtv-tenancy-tests-'+instance)
def runner():
 p=subprocess.run(['docker','inspect',runner_name],capture_output=True,text=True,stdin=subprocess.DEVNULL)
 if p.returncode:
  if re.search(r'not found|no such',p.stderr,re.I):return None
  fail('Could not inspect the owned test runner.')
 d=json.loads(p.stdout)[0]
 labels=d['Config'].get('Labels',{})
 if labels.get('com.88jj.jjtv.purpose')!='jjtv-tenancy-tests' or labels.get('com.88jj.jjtv.instance')!=instance or labels.get('com.88jj.jjtv.role')!='runner':
  fail('Refusing to touch an unowned test runner.')
 if d['HostConfig']['NetworkMode']!=network or not re.fullmatch(r'jjtv-api:[0-9a-f]{40}',d['Config']['Image']):
  fail('Unexpected test runner configuration.')
 return d
def remove_runner():
 if runner():command(['docker','rm','--force',runner_name])
def remove_scratch():
 if not scratch.exists() and not scratch.is_symlink():return
 if scratch.parent!=Path('/tmp') or scratch.name!='jjtv-tenancy-tests-'+instance or scratch.resolve()!=scratch or not scratch.is_dir():
  fail('Refusing to remove an unexpected test source path.')
 marker=scratch/'.jjtv-owner.json'
 if marker.is_symlink() or not marker.is_file() or json.loads(marker.read_text()).get('instance_id')!=instance:
  fail('Refusing to remove test source without its ownership marker.')
 shutil.rmtree(scratch)
 if scratch.exists():fail('Test source cleanup did not complete.')
if action=='cleanup':
 remove_runner()
 remove_scratch()
def inspect(spec):
 name,image,port,host_port=spec
 p=subprocess.run(['docker','inspect',name],capture_output=True,text=True,stdin=subprocess.DEVNULL)
 if p.returncode:
  if re.search(r'not found|no such',p.stderr,re.I):return None
  fail('Could not inspect a tenancy test service.')
 d=json.loads(p.stdout)[0]
 labels=d['Config'].get('Labels',{})
 if labels.get('com.88jj.jjtv.purpose')!='jjtv-tenancy-tests' or labels.get('com.88jj.jjtv.instance')!=instance:
  fail('Refusing to touch an unowned container: '+name)
 if d['Config']['Image']!=image:fail('Unexpected test image: '+name)
 if d['HostConfig']['NetworkMode']!=network:fail('Unexpected test network: '+name)
 binding=d['HostConfig'].get('PortBindings',{}).get(port,[])
 if binding!=[{'HostIp':'127.0.0.1','HostPort':host_port}]:fail('Unexpected test port binding: '+name)
 return d
for spec in specs:
 name,image,port,host_port=spec
 d=inspect(spec)
 if action=='cleanup':
  if d:command(['docker','rm','--force',name])
  print(name+': removed' if d else name+': absent')
  continue
 if action=='create' and not d:
  args=['docker','run','--detach','--name',name,'--label','com.88jj.jjtv.purpose=jjtv-tenancy-tests',
        '--label','com.88jj.jjtv.instance='+instance,'--network',network,
        '--publish','127.0.0.1:'+host_port+':'+port.split('/')[0],
        '--memory','768m','--cpus','1','--health-interval','2s','--health-timeout','2s','--health-retries','30']
  if 'postgres' in name:
   args+=['--tmpfs','/var/lib/postgresql/data:rw,size=768m',
          '--env','POSTGRES_USER=jjtv_tenancy_test','--env','POSTGRES_DB=postgres',
          '--env','POSTGRES_PASSWORD='+request['postgres_password'],
          '--health-cmd','pg_isready -U jjtv_tenancy_test -d postgres',image]
  else:
   args+=['--tmpfs','/data:rw,size=128m','--env','REDIS_PASSWORD='+request['redis_password'],
          '--env','REDISCLI_AUTH='+request['redis_password'],'--health-cmd','redis-cli ping',image,
          'sh','-ec','exec redis-server --appendonly no --save "" --requirepass "$REDIS_PASSWORD"']
  command(args)
  d=inspect(spec)
 if action=='create' and d and not d['State']['Running']:
  command(['docker','start',name])
 if action=='create':
  for _ in range(90):
   d=inspect(spec)
   if d and d['State'].get('Health',{}).get('Status')=='healthy':break
   time.sleep(1)
  else:fail('Test service did not become healthy: '+name)
 d=inspect(spec)
 if not d:fail('Missing test service: '+name)
 if not d['State']['Running']:fail('Stopped test service: '+name)
 status=d['State'].get('Health',{}).get('Status',d['State']['Status'])
 print(name+': '+status+' on 127.0.0.1:'+host_port)
 if status!='healthy':fail('Unhealthy test service: '+name)
if action=='cleanup' and network_result.returncode==0:
 command(['docker','network','rm',network])
 print('Owned tenancy test network removed.')
'''

REMOTE_RUN = r'''
if action=='remote-run':
 import base64,hashlib,io,os,signal,tarfile
 from pathlib import PurePosixPath
 image=request['image']
 source_sha=request['source_sha']
 if not re.fullmatch(r'jjtv-api:[0-9a-f]{40}',image) or not re.fullmatch(r'[0-9a-f]{40}',source_sha):
  fail('Invalid test image or source revision.')
 image_data=json.loads(command(['docker','image','inspect',image]))[0]
 for setting in image_data['Config'].get('Env',[]):
  key,_,value=setting.partition('=')
  if value and (key in ['DATABASE_URL','REDIS_URL'] or re.search(r'(SECRET|PASSWORD|TOKEN|API_KEY)',key)):
   fail('The test image unexpectedly contains application credentials.')
 if runner() or scratch.exists() or scratch.is_symlink():
  fail('An earlier owned test runner/source remains; inspect it or use cleanup before retrying.')
 test_args=request['test_args']
 selected=[]
 if not isinstance(test_args,list) or not all(isinstance(x,str) and len(x)<1000 for x in test_args):
  fail('Invalid pytest selection.')
 for arg in test_args:
  name=arg.split('::',1)[0]
  if name.endswith('.py'):
   path=PurePosixPath(name)
   if path.is_absolute() or '..' in path.parts or path.parts[:1]!=('tests',):fail('Tests must be explicit backend tests paths.')
   selected.append(name)
 if not selected:fail('At least one explicit tests/*.py path is required.')
 compressed=base64.b64decode(request['archive_base64'],validate=True)
 if len(compressed)>10*1024*1024 or hashlib.sha256(compressed).hexdigest()!=request['archive_sha256']:
  fail('Invalid test source archive.')
 owned=False
 process=None
 def stop(signum,frame):raise SystemExit(128+signum)
 for signum in [signal.SIGTERM,signal.SIGINT,signal.SIGHUP]:signal.signal(signum,stop)
 try:
  scratch.mkdir(mode=0o700)
  (scratch/'.jjtv-owner.json').write_text(json.dumps({'instance_id':instance,'source_sha':source_sha}))
  owned=True
  total=0
  with tarfile.open(fileobj=io.BytesIO(compressed),mode='r:gz') as archive:
   for member in archive.getmembers():
    parts=PurePosixPath(member.name).parts
    if not parts or parts[0]!='backend' or '..' in parts or PurePosixPath(member.name).is_absolute():fail('Unsafe test archive path.')
    if any(part in ['.venv','__pycache__','.pytest_cache','node_modules'] or part.startswith('.env') for part in parts):fail('Private files are forbidden in the test archive.')
    if not member.isfile() and not member.isdir():fail('Links and special files are forbidden in the test archive.')
    destination=scratch.joinpath(*parts)
    if not destination.resolve().is_relative_to(scratch):fail('Test source escaped its owned directory.')
    if member.isdir():destination.mkdir(parents=True,exist_ok=True)
    else:
     total+=member.size
     if total>100*1024*1024:fail('Test source exceeds its size limit.')
     destination.parent.mkdir(parents=True,exist_ok=True)
     with archive.extractfile(member) as src,destination.open('xb') as dst:shutil.copyfileobj(src,dst)
     destination.chmod(0o644)
  for path in selected:
   if not (scratch/'backend'/path).is_file():fail('A selected test is missing from committed source.')
  env={
   'DATABASE_URL':'postgresql+psycopg://jjtv_tenancy_test:'+request['postgres_password']+'@jjtv-tenancy-test-postgres:5432/postgres',
   'MINITV_TEST_DB':'jjtv_tenancy_test','MINITV_TEST_REDIS_DB':'15',
   'REDIS_URL':'redis://:'+request['redis_password']+'@jjtv-tenancy-test-redis:6379/15',
   'TENANT_CONNECTION_KEY':request['connection_key'],'INDIA_API_BASE_URL':'https://8jj.ai/v1',
   'APP_ENV':'test','BUNNY_MODE':'fake','RESEND_API_KEY':'','RESEND_FROM':'','OPENAI_API_KEY':'',
   'MODERATION_WORKER':'0','PYTHONPATH':'/work/backend','PYTHONDONTWRITEBYTECODE':'1',
   'PYTEST_ADDOPTS':'-p no:cacheprovider',
  }
  args=['docker','run','--rm','--pull','never','--name',runner_name,
        '--label','com.88jj.jjtv.purpose=jjtv-tenancy-tests','--label','com.88jj.jjtv.instance='+instance,
        '--label','com.88jj.jjtv.role=runner','--label','com.88jj.jjtv.source='+source_sha,
        '--network',network,'--read-only','--tmpfs','/tmp:rw,size=1g','--memory','1536m','--cpus','2',
        '--cap-drop','ALL','--security-opt','no-new-privileges',
        '--mount','type=bind,src='+str(scratch/'backend')+',dst=/work/backend,readonly',
        '--workdir','/work/backend','--entrypoint','python']
  for key in env:args+=['--env',key]
  args += [image,'-m','pytest',*test_args]
  print('Remote tenancy pytest source='+source_sha+'; explicit files='+str(len(selected))+'; isolated services only.',flush=True)
  process=subprocess.Popen(args,env={**os.environ,**env},stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
  for line in process.stdout:
   for value in secrets:line=line.replace(value,'[redacted]')
   print(line,end='',flush=True)
  code=process.wait()
 finally:
  remove_runner()
  if process is not None and process.poll() is None:
   process.terminate()
   try:process.wait(timeout=5)
   except subprocess.TimeoutExpired:process.kill();process.wait()
  if owned:remove_scratch()
  print('Owned remote test runner/source cleanup verified.',flush=True)
 raise SystemExit(code)
'''


def require_release_lock() -> None:
    import fcntl

    common = Path(subprocess.check_output(["git", "rev-parse", "--git-common-dir"], cwd=ROOT, text=True).strip())
    if not common.is_absolute():
        common = ROOT / common
    lock = common / "minitv-release.lock"
    if not lock.is_file():
        raise SystemExit("Use release.py run --target video-test-services for create/cleanup/remote-run.")
    with lock.open("r+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            fcntl.flock(handle, fcntl.LOCK_UN)
            raise SystemExit("The repository release lock is not held.")
        handle.seek(0)
        meta = json.load(handle)
        if meta.get("target") != "video-test-services":
            raise SystemExit("The active release target must be video-test-services.")
        if meta.get("pid") != os.getppid() or meta.get("host") != socket.gethostname():
            raise SystemExit("The release lock must be held by this command's parent process on this host.")


def credentials(path: Path, create: bool) -> dict:
    if not path.exists():
        if not create:
            raise SystemExit("The local test credential file is missing; provision the test services first.")
        path.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "instance_id": secrets.token_hex(16),
            "postgres_password": secrets.token_urlsafe(32),
            "redis_password": secrets.token_urlsafe(32),
            "connection_key": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        }
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle)
    return json.loads(path.read_text())


def ssh_command(args: argparse.Namespace) -> list[str]:
    binary = args.ssh_bin or os.environ.get("JJTV_SSH_BIN")
    if not binary:
        git_ssh = Path("D:/Program Files/Git/usr/bin/ssh.exe")
        binary = str(git_ssh) if os.name == "nt" and git_ssh.is_file() else "ssh"
    command = [binary, *shlex.split(os.environ.get("JJTV_SSH_OPTIONS", ""))]
    return command + ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=15", args.host]


def selected_tests(test_args: list[str], remote: bool = False) -> list[str]:
    if test_args[:1] == ["--"]:
        test_args = test_args[1:]
    if not test_args:
        raise SystemExit("Supply explicit pytest files; the full suite is not selected implicitly.")
    if not remote:
        return test_args
    normalized = []
    files = 0
    for arg in test_args:
        path, separator, selector = arg.partition("::")
        if path.endswith(".py"):
            if path.startswith("backend/"):
                path = path[len("backend/"):]
            parts = PurePosixPath(path).parts
            if not parts or parts[0] != "tests" or ".." in parts or PurePosixPath(path).is_absolute():
                raise SystemExit("Remote test files must be explicit backend/tests/*.py paths.")
            arg = path + (separator + selector if separator else "")
            files += 1
        normalized.append(arg)
    if not files:
        raise SystemExit("Remote runs require at least one explicit tests/*.py file.")
    return normalized


def source_archive() -> tuple[str, bytes]:
    status = subprocess.check_output(["git", "status", "--porcelain", "--", "backend"], cwd=ROOT, text=True)
    if status.strip():
        raise SystemExit("Commit the backend source and tests before remote-run.")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    committed = subprocess.check_output(["git", "-c", "core.autocrlf=false", "archive", "--format=tar", sha, "backend"], cwd=ROOT)
    output = io.BytesIO()
    blocked = {".venv", "__pycache__", ".pytest_cache", "node_modules"}
    with tarfile.open(fileobj=io.BytesIO(committed), mode="r:") as source, tarfile.open(fileobj=output, mode="w:gz") as target:
        for member in source.getmembers():
            parts = PurePosixPath(member.name).parts
            if any(part in blocked or part.startswith(".env") for part in parts):
                continue
            if not parts or parts[0] != "backend" or ".." in parts or PurePosixPath(member.name).is_absolute():
                raise SystemExit("Unexpected source archive path.")
            if not member.isfile() and not member.isdir():
                raise SystemExit("Links and special files are not allowed in remote test source.")
            target.addfile(member, source.extractfile(member) if member.isfile() else None)
    return sha, output.getvalue()


def run_remote_tests(ssh: list[str], state: dict, test_args: list[str], image: str) -> int:
    test_args = selected_tests(test_args, remote=True)
    sha, archive = source_archive()
    request = {**state, "action": "remote-run", "test_args": test_args, "image": image,
               "source_sha": sha, "archive_sha256": hashlib.sha256(archive).hexdigest(),
               "archive_base64": base64.b64encode(archive).decode()}
    print(f"Uploading committed backend {sha} for isolated remote pytest; no environment files included.", flush=True)
    process = subprocess.Popen(ssh + ["python3 -c " + shlex.quote(REMOTE + REMOTE_RUN)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        process.stdin.write(json.dumps(request).encode())
        process.stdin.close()
        for raw in process.stdout:
            line = raw.decode(errors="replace")
            for key in ("postgres_password", "redis_password", "connection_key"):
                line = line.replace(state[key], "[redacted]")
            print(line, end="", flush=True)
        return process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def run_tests(ssh: list[str], state: dict, test_args: list[str]) -> int:
    import psycopg
    import redis

    test_args = selected_tests(test_args)
    forward = ssh[:-1] + ["-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15", "-N",
                          "-L", f"127.0.0.1:{POSTGRES_PORT}:127.0.0.1:{POSTGRES_PORT}",
                          "-L", f"127.0.0.1:{REDIS_PORT}:127.0.0.1:{REDIS_PORT}", ssh[-1]]
    tunnel = subprocess.Popen(forward, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    def stop(signum, frame):
        raise SystemExit(128 + signum)
    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, stop)
    if hasattr(signal, "SIGHUP"):
        previous[signal.SIGHUP] = signal.signal(signal.SIGHUP, stop)
    try:
        for _ in range(100):
            if tunnel.poll() is not None:
                raise SystemExit("The test SSH tunnel failed; ensure the two local ports are unused.")
            try:
                with socket.create_connection(("127.0.0.1", POSTGRES_PORT), timeout=0.2), \
                     socket.create_connection(("127.0.0.1", REDIS_PORT), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise SystemExit("Timed out opening the isolated test SSH tunnel.")
        # Authenticate to the dedicated instance before allowing destructive fixtures.
        with psycopg.connect(host="127.0.0.1", port=POSTGRES_PORT, user=TEST_DATABASE,
                             password=state["postgres_password"], dbname="postgres", connect_timeout=5) as conn:
            if conn.execute("SELECT current_user").fetchone()[0] != TEST_DATABASE:
                raise SystemExit("Unexpected database identity; refusing to run test fixtures.")
        redis.Redis(host="127.0.0.1", port=REDIS_PORT, password=state["redis_password"], db=15).ping()
        env = dict(os.environ)
        env.update({
            "DATABASE_URL": f"postgresql+psycopg://{TEST_DATABASE}:{state['postgres_password']}@127.0.0.1:{POSTGRES_PORT}/postgres",
            "MINITV_TEST_DB": TEST_DATABASE, "MINITV_TEST_REDIS_DB": "15",
            "REDIS_URL": f"redis://:{state['redis_password']}@127.0.0.1:{REDIS_PORT}/15",
            "TENANT_CONNECTION_KEY": state["connection_key"], "APP_ENV": "test", "BUNNY_MODE": "fake",
        })
        print("Running pytest against disposable tenancy PostgreSQL and Redis DB 15; no production credentials loaded.", flush=True)
        return subprocess.run([sys.executable, "-m", "pytest", *test_args], cwd=ROOT, env=env, stdin=subprocess.DEVNULL).returncode
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        if tunnel.poll() is None:
            tunnel.terminate()
        try:
            tunnel.wait(timeout=5)
        except subprocess.TimeoutExpired:
            tunnel.kill()
            tunnel.wait()
        if tunnel.stderr is not None:
            tunnel.stderr.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "check", "cleanup", "run", "remote-run"))
    parser.add_argument("--host", default=os.environ.get("JJTV_DEPLOY_HOST", "video"))
    parser.add_argument("--ssh-bin")
    parser.add_argument("--image", default=DEFAULT_TEST_IMAGE, help="Existing credential-free JJTV API image for remote-run.")
    parser.add_argument("--credentials", type=Path, default=Path(tempfile.gettempdir()) / "jjtv-tenancy-test-services.json")
    args, pytest_args = parser.parse_known_args()
    if args.action not in {"run", "remote-run"} and pytest_args:
        parser.error("Unexpected arguments: " + " ".join(pytest_args))
    if args.action in {"create", "cleanup", "remote-run"}:
        require_release_lock()
    state = credentials(args.credentials, args.action == "create")
    ssh = ssh_command(args)
    if args.action == "run":
        return run_tests(ssh, state, pytest_args)
    if args.action == "remote-run":
        return run_remote_tests(ssh, state, pytest_args, args.image)
    request = {name: state[name] for name in ("instance_id", "postgres_password", "redis_password")}
    request["action"] = args.action
    proc = subprocess.run(ssh + ["python3 -c " + shlex.quote(REMOTE)], input=json.dumps(request).encode(),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output = (proc.stdout + proc.stderr).decode(errors="replace")
    for name in ("postgres_password", "redis_password", "connection_key"):
        output = output.replace(state[name], "[redacted]")
    print(output, end="")
    if proc.returncode == 0 and args.action == "cleanup":
        args.credentials.unlink()
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
