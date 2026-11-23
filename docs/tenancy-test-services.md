# Disposable tenancy test services

The test fixture drops and recreates `MINITV_TEST_DB` and flushes Redis DB 15.
Use only the disposable test services provisioned by this script. Never load
the JJTV or India runtime environment into the test process.

The PostgreSQL 16 and Redis 7 containers have fixed test-only names, ownership
labels, loopback ports `15433` and `16380`, memory limits, and disposable tmpfs
data. Creation and cleanup refuse containers whose labels or bindings do not
match the local private credential file. No production volume or network is
used. Random credentials are stored in the local user's private temporary
directory and are never printed. When crossing WSL/Windows, explicitly use the
same credential file via its respective `/mnt/c/...` and `C:/...` paths.

Commit the source before creating the services through the release wrapper:

```bash
python3 scripts/release.py status
python3 scripts/release.py run --target video-test-services --note "Disposable tenancy test dependencies" -- \
  python3 scripts/test_tenancy_services.py --credentials /mnt/c/Users/Administrator/.codex/private/jjtv-tenancy-test-services.json create
python3 scripts/release.py status
```

For database-heavy regressions, execute the selected tests beside the isolated
services to avoid the latency of a database SSH tunnel. Commit the backend source
and selected tests first, then run under the same release lock:

```bash
python3 scripts/release.py run --target video-test-services --note "Remote tenancy regression tests" -- \
  python3 scripts/test_tenancy_services.py --credentials /mnt/c/Users/Administrator/.codex/private/jjtv-tenancy-test-services.json \
  remote-run -- tests/test_tenancy.py -q
```

`remote-run` archives only committed `backend/` source and rejects private
environment files, virtual environments, caches, links, and special files. It
validates the owned test network and containers, extracts into the exact
`/tmp/jjtv-tenancy-tests-<instance-id>` directory, and mounts the backend read-only
at `/work/backend`. The existing credential-free
`jjtv-api:f6428e9dfaa18148e012eb7bcf5da5c4ab7e50f5` image supplies Python and
dependencies; `--image jjtv-api:<40-character-source-sha>` can select another
already-present JJTV API image. The runner never loads a production environment
or attaches production volumes/networks. Test-only PostgreSQL, Redis DB 15,
Fernet, fake-media, and disabled-mail settings are supplied explicitly. Pytest
paths must name files under `tests/` (`backend/tests/` is also accepted).

Runner output redacts test passwords and the test connection key. Normal exit,
test failure, and handled termination clean up only the owned runner and source
directory. Cleanup verifies the resolved directory is the exact owned `/tmp`
path and checks its instance marker before removal. If an uncatchable process
kill leaves artifacts, `cleanup` removes only resources with the matching
ownership labels/marker; it refuses unrelated containers or paths.

The local Windows `run` mode remains available for short tests. It opens an SSH
tunnel, authenticates to the isolated database and Redis, sets test-only
environment variables, and closes only the tunnel process it created. Handled
interrupt/termination signals trigger the same cleanup; it never sweeps other
SSH processes by name:

```powershell
.venv/Scripts/python.exe scripts/test_tenancy_services.py --credentials C:/Users/Administrator/.codex/private/jjtv-tenancy-test-services.json run -- backend/tests/test_tenancy.py -q
```

`DATABASE_URL` initially points at the isolated instance's bootstrap `postgres`
database because the fixture must connect to a different database while
dropping `jjtv_tenancy_test`. The fixture then switches the application and
Alembic to the fixed test database. Redis is always the dedicated instance's
DB 15. The runner does not choose a full test suite implicitly.

After testing, remove only these two owned containers and the local credentials:

```bash
python3 scripts/release.py run --target video-test-services --note "Remove disposable tenancy test services" -- \
  python3 scripts/test_tenancy_services.py --credentials /mnt/c/Users/Administrator/.codex/private/jjtv-tenancy-test-services.json cleanup
```

Use `check` with the same credentials for a read-only health check. On Windows,
the script selects Git's SSH binary when available. WSL uses native SSH with
the existing trusted known_hosts and a temporary private key as described in
the video deployment guide.
