"""Cross-module HTTP contract consumed by the PC account, player and help screens.

Real TestClient + migrated PostgreSQL and Redis. No mocked API payloads or providers.
"""
from fastapi.testclient import TestClient
from app.main import app
from app.modules.account import service as accounts
from app.modules.billing.models import Order
from app.modules.wallet.models import CoinLedgerEntry


def test_pc_profile_history_orders_and_merged_support_http_contract(session,make):
    client=TestClient(app)
    user=make.user("pc-contract@example.com",coins=150);user.public_id="pc-contract"
    subscription=make.subscription(user)
    drama=make.drama("Contract story",status="published",free=1)
    episode=make.episode(drama,1,"published");episode.bunny_video_id="contract-video";episode.duration_seconds=180
    session.add(CoinLedgerEntry(user_id=user.id,amount=150,balance_after=150,biz_type="adjustment",biz_key="pc-contract-credit"))
    order=Order(order_no="PC-CONTRACT-ORDER",user_id=user.id,plan_version_id=subscription.plan_version_id,product_type="membership",product_snapshot={"code":"contract-plan","name":"Contract membership","base_coins":100,"bonus_coins":50},amount_cents=1999,currency="USD",status="paid")
    session.add(order);session.commit()
    headers={"Authorization":"Bearer "+accounts._issue_session(user)}
    me=client.get("/v1/me",headers=headers)
    assert me.status_code==200,me.text
    profile=me.json()
    assert profile["coins"]==150 and profile["public_id"]=="pc-contract"
    assert profile["membership"]["active"] is True and profile["membership"]["until"]
    assert profile["membership"]["billing_mode"] in {"one_time","subscription"}
    assert isinstance(profile["membership"]["canceling"],bool)
    assert client.post("/v1/reactions",headers=headers,json={"target_type":"drama","target_id":drama.id,"kind":"favorite","remove":False}).status_code==200
    favorites=client.get("/v1/me/favorites?limit=1&offset=0",headers=headers).json()
    assert favorites["total"]==1 and favorites["items"][0]["id"]==drama.id
    assert favorites["items"][0]["episode_count"]==1
    assert client.post("/v1/playback/progress",headers=headers,json={"episode_id":episode.id,"position_seconds":45}).status_code==200
    history=client.get("/v1/me/history?limit=1&offset=0",headers=headers).json()
    assert history["items"][0]["episode_id"]==episode.id
    assert history["items"][0]["drama"]["id"]==drama.id
    assert history["items"][0]["position_seconds"]==45 and history["items"][0]["duration_seconds"]==180
    orders=client.get("/v1/commerce/orders?limit=20&offset=0",headers=headers).json()
    purchase=orders["items"][0]
    assert purchase["order_no"]==order.order_no and purchase["product_name"]=="Contract membership"
    assert purchase["coins"]==150 and purchase["amount_cents"]==1999
    assert purchase["status"]=="paid" and isinstance(orders["has_more"],bool)
    ledger=client.get("/v1/me/ledger",headers=headers).json()["items"][0]
    assert ledger["amount"]==150 and ledger["balance_after"]==150 and ledger["biz_type"]=="adjustment"
    tasks=client.get("/v1/me/tasks",headers=headers).json()
    assert tasks["balance"]==150 and isinstance(tasks["checkin"]["checked_today"],bool)
    assert isinstance(tasks["checkin"]["streak"],int) and isinstance(tasks["checkin"]["amount"],int)
    meta=client.get("/v1/support/meta").json()
    category=meta["categories"][0]
    assert category["name_en"] and isinstance(category["fields"],list)
    numbers=[]
    for title in ("First question","Main question"):
        response=client.post("/v1/support/tickets",headers=headers,json={"subject":title,"category":category["code"],"body":"A billing question","order_no":order.order_no})
        assert response.status_code==201,response.text
        numbers.append(response.json()["ticket"]["number"])
    listing=client.get("/v1/support/tickets?page=1&page_size=1",headers=headers).json()
    assert listing["total"]==2 and len(listing["items"])==1 and listing["page_size"]==1
    next_page=client.get("/v1/support/tickets?page=2&page_size=1",headers=headers).json()
    assert listing["items"][0]["number"]!=next_page["items"][0]["number"]
    staff=make.admin(role="admin")
    login=client.post("/admin/v1/auth/login",json={"tenant_code":"jjtv","username":staff.username,"password":"pass-1234"})
    assert login.status_code==200,login.text
    admin={"Authorization":"Bearer "+login.json()["token"]}
    merged=client.post("/admin/v1/support/tickets/"+numbers[0]+"/merge",headers=admin,json={"target_number":numbers[1]})
    assert merged.status_code==200,merged.text
    client.post("/admin/v1/support/tickets/"+numbers[1]+"/messages",headers=admin,json={"body":"Internal customer review","internal":True})
    old=client.get("/v1/support/tickets/"+numbers[0],headers=headers).json()
    assert old["ticket"]["status"]=="merged" and old["ticket"]["can_reply"] is False
    assert old["ticket"]["merged_into"]==numbers[1]
    main=client.get("/v1/support/tickets/"+old["ticket"]["merged_into"],headers=headers).json()
    assert len(main["messages"])==2
    assert all(m["body"]!="Internal customer review" for m in main["messages"])
    assert "customer" not in main and "events" not in main
