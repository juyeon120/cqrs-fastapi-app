"""Deterministic unit tests for the CQRS API.

No MariaDB, MongoDB or Kafka socket is ever opened: the module-level handles in ``main``
(``db_pool``, ``mongo_db``, ``producer``) are replaced with mocks and the ASGI app is called
in-process *without* running its lifespan (which would try to connect to the real services).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio

import main

VALID_ORDER = {"customer": "alice", "item": "keyboard", "quantity": 2, "amount": 49.5}
EVENT_KEYS = {"id", "customer", "item", "quantity", "amount", "status", "created_at"}


def make_db_pool():
    """Mimic ``aiomysql`` pool -> connection -> cursor async context managers."""
    cursor = MagicMock()
    cursor.execute = AsyncMock()
    conn = MagicMock()
    conn.cursor.return_value.__aenter__.return_value = cursor
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    return pool, cursor


@pytest.fixture
def db():
    pool, cursor = make_db_pool()
    return pool, cursor


@pytest.fixture
def kafka():
    producer = MagicMock()
    producer.send_and_wait = AsyncMock()
    return producer


@pytest.fixture
def mongo():
    orders = MagicMock()
    orders.find_one = AsyncMock(return_value=None)
    mongo_db = MagicMock()
    mongo_db.orders = orders
    return mongo_db


@pytest.fixture
def wired(monkeypatch, db, kafka, mongo):
    pool, cursor = db
    monkeypatch.setattr(main, "db_pool", pool)
    monkeypatch.setattr(main, "producer", kafka)
    monkeypatch.setattr(main, "mongo_db", mongo)
    return {"pool": pool, "cursor": cursor, "producer": kafka, "mongo": mongo}


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=main.app)  # does not run lifespan
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def drain_background_tasks():
    """Wait for fire-and-forget tasks (the Kafka publish) so assertions are deterministic."""
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending)


# ---------------------------------------------------------------- Command: POST /orders

@pytest.mark.asyncio
async def test_post_orders_returns_201_with_event_schema(client, wired):
    resp = await client.post("/orders", json=VALID_ORDER)
    await drain_background_tasks()

    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == EVENT_KEYS
    assert body["status"] == "CREATED"
    assert {k: body[k] for k in VALID_ORDER} == VALID_ORDER


@pytest.mark.asyncio
async def test_post_orders_writes_to_command_store(client, wired):
    resp = await client.post("/orders", json=VALID_ORDER)
    await drain_background_tasks()

    wired["cursor"].execute.assert_awaited_once()
    sql, params = wired["cursor"].execute.await_args.args
    assert sql.startswith("INSERT INTO orders")
    assert params[0] == resp.json()["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"customer": "alice", "item": "keyboard", "quantity": 2},  # amount missing
        {**VALID_ORDER, "quantity": "many"},  # wrong type
        {**VALID_ORDER, "amount": "free"},  # wrong type
        {**VALID_ORDER, "customer": None},  # null
    ],
)
async def test_post_orders_rejects_invalid_payload(client, wired, payload):
    resp = await client.post("/orders", json=payload)
    await drain_background_tasks()

    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], list)
    wired["cursor"].execute.assert_not_called()
    wired["producer"].send_and_wait.assert_not_called()


# ------------------------------------------------------------------ Query: GET /orders

@pytest.mark.asyncio
async def test_get_orders_returns_read_model_document(client, wired):
    doc = {**VALID_ORDER, "id": "abc-123", "status": "CREATED", "created_at": "2026-01-01T00:00:00"}
    wired["mongo"].orders.find_one.return_value = doc

    resp = await client.get("/orders", params={"id": "abc-123"})

    assert resp.status_code == 200
    assert set(resp.json()) == EVENT_KEYS
    assert resp.json()["id"] == "abc-123"
    wired["mongo"].orders.find_one.assert_awaited_once_with({"id": "abc-123"}, {"_id": 0})


@pytest.mark.asyncio
async def test_get_orders_unknown_id_returns_404(client, wired):
    resp = await client.get("/orders", params={"id": "missing"})

    assert resp.status_code == 404
    assert resp.json() == {"detail": "Order not found in Read DB"}


@pytest.mark.asyncio
async def test_get_orders_requires_id_query_param(client, wired):
    resp = await client.get("/orders")

    assert resp.status_code == 422
    wired["mongo"].orders.find_one.assert_not_called()


# ------------------------------------------------------------------ Kafka (mocked)

@pytest.mark.asyncio
async def test_post_orders_dispatches_event_to_kafka(client, wired):
    resp = await client.post("/orders", json=VALID_ORDER)
    await drain_background_tasks()

    wired["producer"].send_and_wait.assert_awaited_once_with(main.KAFKA_TOPIC, resp.json())


@pytest.mark.asyncio
async def test_kafka_failure_does_not_fail_the_command(client, wired):
    wired["producer"].send_and_wait.side_effect = RuntimeError("broker down")

    resp = await client.post("/orders", json=VALID_ORDER)
    await drain_background_tasks()

    assert resp.status_code == 201
    wired["producer"].send_and_wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_safe_kafka_send_without_producer_is_noop(monkeypatch):
    monkeypatch.setattr(main, "producer", None)

    await main.safe_kafka_send("any-topic", {"id": "1"})  # must not raise
