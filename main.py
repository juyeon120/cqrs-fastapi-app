import asyncio
import json
import os
import uuid
import logging
from datetime import datetime
from contextlib import asynccontextmanager

import aiomysql
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cqrs-app")

MARIADB_HOST = os.getenv("MARIADB_HOST", "mariadb-svc")
MARIADB_PORT = int(os.getenv("MARIADB_PORT", "3306"))
MARIADB_USER = os.getenv("MARIADB_USER", "user")
MARIADB_PASSWORD = os.getenv("MARIADB_PASSWORD", "password")
MARIADB_DB = os.getenv("MARIADB_DB", "orders_db")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo-svc:27017")
MONGO_DB = os.getenv("MONGO_DB", "read_db")

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka-svc:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "order-events")

db_pool = None
mongo_client = None
mongo_db = None
producer = None

class CreateOrderRequest(BaseModel):
    customer: str
    item: str
    quantity: int
    amount: float

async def kafka_consumer_worker():
    consumer = AIOKafkaConsumer(
        KAFKA_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id="order-sync-group",
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
    )
    while True:
        try:
            await consumer.start()
            logger.info("Kafka consumer connected successfully.")
            break
        except Exception as e:
            logger.warning(f"Waiting for Kafka broker...: {e}")
            await asyncio.sleep(2)

    try:
        async for msg in consumer:
            try:
                event = msg.value
                await mongo_db.orders.update_one(
                    {"id": event["id"]},
                    {"$set": event},
                    upsert=True,
                )
            except Exception as e:
                logger.error(f"Error syncing to MongoDB: {e}")
    finally:
        await consumer.stop()

async def safe_kafka_send(topic: str, payload: dict):
    """카프카 브로커 장애가 발생해도 메인 웹 스레드를 블로킹하지 않는 안전 전송 루틴"""
    try:
        if producer:
            await producer.send_and_wait(topic, payload)
    except Exception as e:
        logger.error(f"Failed to publish event to Kafka (Async): {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool, mongo_client, mongo_db, producer

    for attempt in range(1, 31):
        try:
            db_pool = await aiomysql.create_pool(
                host=MARIADB_HOST,
                port=MARIADB_PORT,
                user=MARIADB_USER,
                password=MARIADB_PASSWORD,
                db=MARIADB_DB,
                autocommit=True,
            )
            logger.info("MariaDB connected successfully.")
            break
        except Exception as e:
            logger.warning(f"Waiting for MariaDB ({attempt}/30)...: {e}")
            await asyncio.sleep(2)
    else:
        raise RuntimeError("Failed to connect to MariaDB after 30 attempts")

    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id VARCHAR(64) PRIMARY KEY,
                    customer VARCHAR(64),
                    item VARCHAR(128),
                    quantity INT,
                    amount DOUBLE,
                    status VARCHAR(32),
                    created_at VARCHAR(64)
                )
            """)
            # 기존 배포에서 이미 생성된 테이블에도 신규 컬럼을 안전하게 추가
            await cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS item VARCHAR(128) DEFAULT ''")
            await cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS quantity INT DEFAULT 1")

    mongo_client = AsyncIOMotorClient(MONGO_URI)
    mongo_db = mongo_client[MONGO_DB]

    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    try:
        await producer.start()
    except Exception as e:
        logger.warning(f"Kafka producer initial connect deferred: {e}")

    consumer_task = asyncio.create_task(kafka_consumer_worker())

    yield

    consumer_task.cancel()
    if producer:
        await producer.stop()
    mongo_client.close()
    db_pool.close()
    await db_pool.wait_closed()

app = FastAPI(lifespan=lifespan)

@app.get("/healthz")
async def healthz():
    return {"ok": True}

@app.get("/", response_class=HTMLResponse)
async def web_index():
    # 최신 생성일자 역순(내림차순, -1)으로 정렬하여 최근 데이터가 최상단에 노출
    orders = await mongo_db.orders.find().sort("created_at", -1).to_list(300)
    
    rows = "".join([
        f"<tr><td><code>{o.get('id')[:8]}...</code></td><td><b>{o.get('customer')}</b></td><td>{o.get('item')}</td><td>{o.get('quantity')}</td><td>{o.get('amount'):,.1f}</td><td><span style='color:green; font-weight:600;'>{o.get('status')}</span></td><td>{o.get('created_at')}</td></tr>"
        for o in orders
    ])
    
    total_count = len(orders)
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>CQRS 클라우드 주문 시스템</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 30px auto; max-width: 960px; background: #f8fafc; color: #1e293b; }}
            .card {{ background: white; padding: 24px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); margin-bottom: 24px; border: 1px solid #e2e8f0; }}
            input, button {{ padding: 10px 14px; margin-right: 8px; border-radius: 6px; border: 1px solid #cbd5e1; font-size: 14px; }}
            button {{ background: #2563eb; color: white; border: none; cursor: pointer; font-weight: 600; transition: 0.2s; }}
            button:hover {{ background: #1d4ed8; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 13px; }}
            th, td {{ padding: 12px 14px; border-bottom: 1px solid #e2e8f0; text-align: left; }}
            th {{ background: #f1f5f9; color: #475569; font-weight: 600; }}
            .badge {{ background: #e0f2fe; color: #0369a1; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h2>CQRS 실시간 주문 시스템</h2>
            <p style="color: #64748b; font-size: 13px;">[Command Write] MariaDB ➔ [Event Stream] Kafka ➔ [Query Read] MongoDB</p>
            <form onsubmit="event.preventDefault(); submitOrder();">
                <input id="customer" placeholder="고객명" required />
                <input id="item" placeholder="상품명" required />
                <input id="quantity" type="number" step="1" min="1" placeholder="수량" required />
                <input id="amount" type="number" step="any" placeholder="금액" required />
                <button type="submit">주문 생성</button>
                <button type="button" style="background:#64748b;" onclick="window.location.reload();">새로고침</button>
            </form>
        </div>

        <div class="card">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <h3>Read Model (MongoDB) 동기화 내역</h3>
                <span class="badge">총 동기화: {total_count} 건</span>
            </div>
            <table>
                <thead>
                    <tr><th>주문 ID (Prefix)</th><th>고객명</th><th>상품명</th><th>수량</th><th>금액</th><th>상태</th><th>생성일시</th></tr>
                </thead>
                <tbody id="order-rows">
                    {rows if rows else '<tr><td colspan="7" align="center" style="color:#94a3b8; padding:24px;">주문 내역이 없습니다.</td></tr>'}
                </tbody>
            </table>
        </div>

        <script>
            async function submitOrder() {{
                const customer = document.getElementById('customer').value;
                const item = document.getElementById('item').value;
                const quantity = parseInt(document.getElementById('quantity').value, 10);
                const amount = parseFloat(document.getElementById('amount').value);
                await fetch('/orders', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ customer, item, quantity, amount }})
                }});
                document.getElementById('customer').value = '';
                document.getElementById('item').value = '';
                document.getElementById('quantity').value = '';
                document.getElementById('amount').value = '';
                setTimeout(() => window.location.reload(), 500);
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@app.post("/orders", status_code=201)
async def create_order(payload: CreateOrderRequest):
    order_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()
    status = "CREATED"

    # 1. MariaDB (Command 저장소)에 쓰기
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO orders (id, customer, item, quantity, amount, status, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (order_id, payload.customer, payload.item, payload.quantity, payload.amount, status, created_at),
            )

    event_payload = {
        "id": order_id,
        "customer": payload.customer,
        "item": payload.item,
        "quantity": payload.quantity,
        "amount": payload.amount,
        "status": status,
        "created_at": created_at,
    }

    # 2. Kafka 전송을 논블로킹 백그라운드 태스크로 처리하여 사용자 응답 속도 보장
    asyncio.create_task(safe_kafka_send(KAFKA_TOPIC, event_payload))
    return event_payload

@app.get("/orders")
async def get_order(id: str):
    order = await mongo_db.orders.find_one({"id": id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found in Read DB")
    return order
