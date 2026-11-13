#!/usr/bin/env python3
"""Provision disposable, isolated PostgreSQL/Redis for JJTV tenancy tests.

create/cleanup must run under release.py --target video-test-services.
check is read-only; run executes local tests against only these disposable services.
Credentials stay in a private local temporary JSON file, never in Git or logs.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import shlex
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
PURPOSE = "jjtv-tenancy-tests"
TEST_DATABASE = "jjtv_tenancy_test"
POSTGRES_PORT = 15433
REDIS_PORT = 16380

REMOTE = r'''
import json,re,subprocess,sys,time
request=json.load(sys.stdin)
action=request['action']
assert action in ['create','check','cleanup']
instance=request['instance_id']
assert re.fullmatch(r'[0-9a-f]{32}',instance)
specs=[('jjtv-tenancy-test-postgres','postgres:16-alpine','5432/tcp','15433'),
       ('jjtv-tenancy-test-redis','redis:7-alpine','6379/tcp','16380')]
network='jjtv-tenancy-tests-'+instance[:12]
secrets=[request['postgres_password'],request['redis_password']]
def fail(message):
 for value in secrets:message=message.replace(value,'[redacted]')
 raise SystemExit(message)
def command(args):
 p=subprocess.run(args,capture_output=True,text=True,stdin=subprocess.DEVNULL)
 if p.returncode:fail('Test service operation failed: '+p.stderr[-1500:])
 return p.stdout
network_result=subprocess.run(['docker','network','inspect',network],capture_output=True,text=True,stdin=subprocess.DEVNULL)
if network_result.returncode==0:
 labels=json.loads(network_result.stdout)[0].get('Labels',{})
 if labels.get('com.88jj.jjtv.purpose')!='jjtv-tenancy-tests' or labels.get('com.88jj.jjtv.instance')!=instance:
  fail('Refusing to touch an unowned network: '+network)
elif action=='create':
 command(['docker','network','create','--label','com.88jj.jjtv.purpose=jjtv-tenancy-tests',
          '--label','com.88jj.jjtv.instance='+instance,network])
def inspect(spec):
 name,image,port,host_port=spec
 p=subprocess.run(['docker','inspect',name],capture_output=True,text=True,stdin=subprocess.DEVNULL)
 if p.returncode:return None
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
 status=d['State'].get('Health',{}).get('Status',d['State']['Status'])
 print(name+': '+status+' on 127.0.0.1:'+host_port)
 if status!='healthy':fail('Unhealthy test service: '+name)
if action=='cleanup' and network_result.returncode==0:
 command(['docker','network','rm',network])
 print('Owned tenancy test network removed.')
'''


def require_release_lock() -> None:
    import fcntl

    common = Path(subprocess.check_output(["git", "rev-parse", "--git-common-dir"], cwd=ROOT, text=True).strip())
    if not common.is_absolute():
        common = ROOT / common
    lock = common / "minitv-release.lock"
    if not lock.is_file():
        raise SystemExit("Use release.py run --target video-test-services for create/cleanup.")
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


def run_tests(ssh: list[str], state: dict, test_args: list[str]) -> int:
    import psycopg
    import redis

    if test_args[:1] == ["--"]:
        test_args = test_args[1:]
    if not test_args:
        raise SystemExit("Supply explicit pytest paths after run --; the full suite is not selected implicitly.")
    forward = ssh[:-1] + ["-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15", "-N",
                          "-L", f"127.0.0.1:{POSTGRES_PORT}:127.0.0.1:{POSTGRES_PORT}",
                          "-L", f"127.0.0.1:{REDIS_PORT}:127.0.0.1:{REDIS_PORT}", ssh[-1]]
    tunnel = subprocess.Popen(forward, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
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
        tunnel.terminate()
        try:
            tunnel.wait(timeout=5)
        except subprocess.TimeoutExpired:
            tunnel.kill()
            tunnel.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "check", "cleanup", "run"))
    parser.add_argument("--host", default=os.environ.get("JJTV_DEPLOY_HOST", "video"))
    parser.add_argument("--ssh-bin")
    parser.add_argument("--credentials", type=Path, default=Path(tempfile.gettempdir()) / "jjtv-tenancy-test-services.json")
    args, pytest_args = parser.parse_known_args()
    if args.action != "run" and pytest_args:
        parser.error("Unexpected arguments: " + " ".join(pytest_args))
    if args.action in {"create", "cleanup"}:
        require_release_lock()
    state = credentials(args.credentials, args.action == "create")
    ssh = ssh_command(args)
    if args.action == "run":
        return run_tests(ssh, state, pytest_args)
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
