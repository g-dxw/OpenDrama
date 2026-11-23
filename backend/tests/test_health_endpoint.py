import json
from contextlib import contextmanager
import psycopg
import redis
from app.main import health


def postgres_ok(monkeypatch, calls):
    class Connection:
        def execute(self, sql):
            return self
        def fetchone(self):
            return ["16.test"]
    @contextmanager
    def connect(url, **kwargs):
        calls.append(url)
        yield Connection()
    monkeypatch.setattr(psycopg, "connect", connect)


def test_health_supports_sqlalchemy_driver_url(monkeypatch):
    calls = []
    postgres_ok(monkeypatch, calls)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unit:unit-secret@localhost/example")
    monkeypatch.setenv("REDIS_URL", "redis://localhost/0")
    class Cache:
        def ping(self): return True
        def info(self, section): return {"redis_version": "7.test"}
    monkeypatch.setattr(redis.Redis, "from_url", lambda *a, **k: Cache())
    assert health()["status"] == "ok"
    assert calls == ["postgresql://unit:unit-secret@localhost/example"]


def test_health_failure_does_not_expose_database_credentials(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unit:unit-secret@localhost/example")
    monkeypatch.setenv("REDIS_URL", "redis://localhost/0")
    def fail(*args, **kwargs):
        raise psycopg.OperationalError("connection failed: unit-secret")
    monkeypatch.setattr(psycopg, "connect", fail)
    response = health()
    assert response.status_code == 503
    assert json.loads(response.body) == {"status": "unavailable", "service": "postgres"}
    assert b"unit-secret" not in response.body


def test_health_reports_cache_failure_without_connection_details(monkeypatch):
    postgres_ok(monkeypatch, [])
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/example")
    monkeypatch.setenv("REDIS_URL", "redis://:unit-secret@localhost/0")
    class Cache:
        def ping(self): raise redis.ConnectionError("unit-secret")
    monkeypatch.setattr(redis.Redis, "from_url", lambda *a, **k: Cache())
    response = health()
    assert response.status_code == 503
    assert json.loads(response.body) == {"status": "unavailable", "service": "redis"}
