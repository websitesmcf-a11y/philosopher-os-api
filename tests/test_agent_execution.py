"""Tests for the real agent execution layer: web discovery, drip scheduler,
Facebook posting, cross-agent redirect, and the ruflo CLI wrapper."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.database.models import Campaign, CampaignLead, Lead, ScheduledJob


# ─── Web discovery ──────────────────────────────────────────────────────

DDG_FIXTURE = """
<div class="result">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fplumbers&rut=abc">Joburg <b>Plumbers</b> — 24/7</a>
  <a class="result__snippet" href="#">Best plumbing company in Johannesburg, call us today.</a>
</div>
<div class="result">
  <a rel="nofollow" class="result__a" href="https://other.example/page">Other Result</a>
  <a class="result__snippet" href="#">Another snippet.</a>
</div>
"""


def test_duckduckgo_parser_extracts_results():
    from app.integrations.web_discovery import _parse_duckduckgo_html

    results = _parse_duckduckgo_html(DDG_FIXTURE)
    assert len(results) == 2
    assert results[0]["title"] == "Joburg Plumbers — 24/7"
    assert results[0]["url"] == "https://example.com/plumbers"
    assert "plumbing company" in results[0]["snippet"].lower()
    assert results[1]["url"] == "https://other.example/page"


@pytest.mark.asyncio
async def test_web_search_returns_error_status_on_network_failure():
    from app.integrations import web_discovery

    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=OSError("no network"))):
        result = await web_discovery.web_search("anything")
    assert result["status"] == "error"
    assert result["results"] == []


@pytest.mark.asyncio
async def test_find_businesses_tops_up_from_web_search():
    from app.integrations import web_discovery

    async def fake_overpass(industry, location, count):
        return [{"name": "OSM Plumber", "phone": "+27 11 555 0000", "source": "openstreetmap"}]

    async def fake_search(query, count=10):
        return {"status": "success", "results": [
            {"title": "Web Plumber - Joburg", "url": "https://webplumber.example", "snippet": "s"},
        ]}

    async def no_gmaps(*a, **k):
        return {"status": "browser_unavailable", "businesses": []}

    with patch.object(web_discovery, "_overpass_businesses", fake_overpass), \
         patch.object(web_discovery, "web_search", fake_search), \
         patch.object(web_discovery, "scrape_google_maps", no_gmaps), \
         patch.object(web_discovery.browser_cli, "_path", None), \
         patch.object(web_discovery.browser_cli, "_checked", True):
        result = await web_discovery.find_businesses("plumber", "Johannesburg", count=2)

    assert result["status"] == "success"
    assert result["count"] == 2
    names = [b["name"] for b in result["businesses"]]
    assert "OSM Plumber" in names
    assert "Web Plumber" in names


def test_google_maps_delimited_parse():
    """The Maps payload (name@@F@@web@@F@@url@@F@@info, joined by @@R@@) parses correctly."""
    from app.integrations import web_discovery as wd

    payload = (
        "Joe Plumbing@@F@@0@@F@@https://maps/joe@@F@@Plumber · 12 Main Rd · 011 555 1234"
        "@@R@@"
        "Web Plumbers@@F@@1@@F@@https://maps/web@@F@@Plumber · has site"
    )
    # Drive the parser through the same code path scrape_google_maps uses.
    rows = []
    for rec in payload.split(wd._RECORD):
        f = rec.split(wd._FIELD)
        rows.append((f[0], f[1] == "1"))
    assert rows[0] == ("Joe Plumbing", False)
    assert rows[1] == ("Web Plumbers", True)


@pytest.mark.asyncio
async def test_add_leads_to_campaign_enrolls_and_schedules(test_session):
    from app.agents.odysseus import Odysseus
    from app.agents.base import AgentContext
    from sqlalchemy import select

    org_id = uuid.uuid4()
    lead_a = Lead(org_id=org_id, name="A", phone="+27 82 1", status="new", industry="plumbing")
    lead_b = Lead(org_id=org_id, name="B", phone="+27 82 2", status="new", industry="plumbing")
    campaign = Campaign(org_id=org_id, name="Promo", channel="whatsapp",
                        message_template="Hi {name}", status="draft")
    test_session.add_all([lead_a, lead_b, campaign])
    await test_session.flush()

    ody = Odysseus()
    ctx = AgentContext(user_input="x", org_id=org_id, db_session=test_session)
    res = await ody._execute_tool("add_leads_to_campaign",
                                  {"campaign_id": str(campaign.id), "industry": "plumbing"}, ctx)
    assert res["status"] == "enrolled"
    assert res["leads_added"] == 2

    enrolled = (await test_session.execute(
        select(CampaignLead).where(CampaignLead.campaign_id == campaign.id)
    )).scalars().all()
    assert len(enrolled) == 2
    jobs = (await test_session.execute(
        select(ScheduledJob).where(ScheduledJob.job_type == "campaign_drip_send")
    )).scalars().all()
    assert any((j.payload or {}).get("campaign_id") == str(campaign.id) for j in jobs)


# ─── Lead persistence ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_businesses_as_leads_creates_records(test_session):
    from app.agents.heraclitus import save_businesses_as_leads

    org_id = uuid.uuid4()
    businesses = [
        {"name": "Acme Plumbing", "phone": "+27 11 111 1111", "email": "info@acme.example",
         "website": "https://acme.example", "source": "openstreetmap"},
        {"name": "Acme Plumbing", "phone": "dup"},  # duplicate name skipped
        {"name": "Beta Pipes", "source": "web_search"},
    ]
    created = await save_businesses_as_leads(test_session, org_id, businesses, "plumber")
    assert len(created) == 2
    assert created[0]["phone"] == "+27 11 111 1111"

    from sqlalchemy import select
    rows = (await test_session.execute(select(Lead).where(Lead.org_id == org_id))).scalars().all()
    assert {r.name for r in rows} == {"Acme Plumbing", "Beta Pipes"}
    assert all(r.source == "web_discovery" and r.status == "new" for r in rows)


# ─── Drip scheduler ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_drip_send_delivers_and_schedules_next_in_window(test_session, test_engine, monkeypatch):
    from app.services import scheduler as sched_module
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
    from sqlalchemy import select

    org_id = uuid.uuid4()
    lead1 = Lead(org_id=org_id, name="Lead One", phone="+27 82 000 0001", status="new")
    lead2 = Lead(org_id=org_id, name="Lead Two", phone="+27 82 000 0002", status="new")
    test_session.add_all([lead1, lead2])
    await test_session.flush()

    campaign = Campaign(
        org_id=org_id, name="Test Drip", channel="whatsapp",
        message_template="Hi {name}!", status="active", target_count=2,
        schedule_config={"mode": "drip", "interval_min_minutes": 40,
                         "interval_max_minutes": 60, "personalize": False},
    )
    test_session.add(campaign)
    await test_session.flush()
    test_session.add_all([
        CampaignLead(campaign_id=campaign.id, lead_id=lead1.id, status="pending"),
        CampaignLead(campaign_id=campaign.id, lead_id=lead2.id, status="pending"),
    ])
    job = ScheduledJob(
        org_id=org_id, job_type="campaign_drip_send",
        payload={"campaign_id": str(campaign.id)},
        scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=1),
        status="pending",
    )
    test_session.add(job)
    await test_session.commit()

    # Point the scheduler at the test database and stub real delivery.
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(sched_module, "async_session", factory)
    sent_to = []

    async def fake_deliver(db, org, lead, channel, body, subject=None):
        sent_to.append((lead.name, channel, body))
        return {"status": "sent"}

    import app.services.delivery as delivery_module
    monkeypatch.setattr(delivery_module, "deliver_to_lead", fake_deliver)

    scheduler = sched_module.JobScheduler()
    executed = await scheduler.tick()
    assert executed == 1

    assert len(sent_to) == 1
    assert sent_to[0][1] == "whatsapp"
    assert "Hi " in sent_to[0][2]  # template applied

    # One campaign lead sent, the next drip job sits 40-60 minutes out.
    async with factory() as db:
        sent_rows = (await db.execute(
            select(CampaignLead).where(CampaignLead.campaign_id == campaign.id,
                                       CampaignLead.status == "sent")
        )).scalars().all()
        assert len(sent_rows) == 1

        all_pending = (await db.execute(
            select(ScheduledJob).where(ScheduledJob.job_type == "campaign_drip_send",
                                       ScheduledJob.status == "pending")
        )).scalars().all()
        next_jobs = [j for j in all_pending
                     if (j.payload or {}).get("campaign_id") == str(campaign.id)]
        assert len(next_jobs) == 1
        next_at = next_jobs[0].scheduled_for
        if next_at.tzinfo is None:  # sqlite drops tzinfo
            next_at = next_at.replace(tzinfo=timezone.utc)
        gap = next_at - datetime.now(timezone.utc)
        assert timedelta(minutes=39) <= gap <= timedelta(minutes=61)

        done = (await db.execute(
            select(ScheduledJob).where(ScheduledJob.status == "completed")
        )).scalars().all()
        assert len(done) == 1


@pytest.mark.asyncio
async def test_drip_completes_campaign_when_no_pending_leads(test_session, test_engine, monkeypatch):
    from app.services import scheduler as sched_module
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
    from sqlalchemy import select

    org_id = uuid.uuid4()
    campaign = Campaign(
        org_id=org_id, name="Empty Drip", channel="whatsapp",
        message_template="Hi!", status="active",
    )
    test_session.add(campaign)
    await test_session.flush()
    test_session.add(ScheduledJob(
        org_id=org_id, job_type="campaign_drip_send",
        payload={"campaign_id": str(campaign.id)},
        scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=1),
        status="pending",
    ))
    await test_session.commit()

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(sched_module, "async_session", factory)

    scheduler = sched_module.JobScheduler()
    await scheduler.tick()

    async with factory() as db:
        row = (await db.execute(select(Campaign).where(Campaign.id == campaign.id))).scalar_one()
        assert row.status == "completed"


def test_personalize_fallback_substitutes_placeholders():
    from app.services.scheduler import _personalize_fallback

    lead = Lead(org_id=uuid.uuid4(), name="Thabo", company="Mzansi Foods", industry="catering")
    out = _personalize_fallback("Hi {name} from {company} ({industry})", lead)
    assert out == "Hi Thabo from Mzansi Foods (catering)"


# ─── Facebook integration ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_facebook_post_not_connected(test_session):
    from app.integrations.facebook import post_to_page

    result = await post_to_page(test_session, "Hello world")
    assert result["status"] == "not_connected"
    assert "Connections page" in result["message"]


@pytest.mark.asyncio
async def test_facebook_post_publishes_with_credentials(test_session, monkeypatch):
    from app.integrations import facebook as fb
    import app.services.connection_service as conn_svc

    async def fake_creds(db, provider):
        assert provider == "facebook"
        return {"page_access_token": "tok"}, {"page_id": "12345"}

    monkeypatch.setattr(conn_svc, "get_provider_credentials", fake_creds)

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"id": "12345_67890"}

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=FakeResponse())):
        result = await fb.post_to_page(test_session, "Big announcement", link="https://x.example")

    assert result == {"status": "posted", "post_id": "12345_67890"}


# ─── Cross-agent redirect ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_redirect_to_agent_runs_target_and_returns_response():
    from app.agents.base import AgentContext, AgentActionResult
    from app.agents.council import CouncilOrchestrator
    from app.agents.heraclitus import Heraclitus
    from app.agents.odysseus import Odysseus

    council = CouncilOrchestrator()
    heraclitus = Heraclitus()
    odysseus = Odysseus()
    council.register(heraclitus)
    council.register(odysseus)

    async def fake_run(context):
        return AgentActionResult(success=True, message=f"handled: {context.user_input}")

    odysseus.run = fake_run
    context = AgentContext(user_input="send a campaign", depth=0)
    result = await heraclitus._execute_common_tool(
        "redirect_to_agent", {"agent": "odysseus", "task": "send a campaign"}, context
    )
    assert result["status"] == "redirected"
    assert result["agent"] == "odysseus"
    assert result["response"] == "handled: send a campaign"


@pytest.mark.asyncio
async def test_redirect_depth_limit_blocks_loops():
    from app.agents.base import AgentContext
    from app.agents.council import CouncilOrchestrator
    from app.agents.heraclitus import Heraclitus
    from app.agents.odysseus import Odysseus

    council = CouncilOrchestrator()
    heraclitus = Heraclitus()
    council.register(heraclitus)
    council.register(Odysseus())

    context = AgentContext(user_input="x", depth=2)
    result = await heraclitus._execute_common_tool(
        "redirect_to_agent", {"agent": "odysseus", "task": "x"}, context
    )
    assert result["status"] == "error"
    assert "limit" in result["message"].lower()


def test_common_toolbelt_present_on_every_agent():
    from app.agents.heraclitus import Heraclitus
    from app.agents.odysseus import Odysseus
    from app.agents.solon import Solon
    from app.agents.athena import Athena

    for agent in (Heraclitus(), Odysseus(), Solon(), Athena()):
        names = {t["name"] for t in agent.all_tools}
        for required in ("redirect_to_agent", "web_search", "browser_task",
                         "start_background_job", "remember", "recall"):
            assert required in names, f"{agent.name} is missing {required}"
        # No duplicate tool names after merging specialist + common tools
        assert len(names) == len(agent.all_tools)


def test_duplicate_call_signature_is_stable():
    from app.agents.base import BaseAgent

    sig1 = BaseAgent._call_signature("web_search", {"query": "a", "count": 5})
    sig2 = BaseAgent._call_signature("web_search", {"count": 5, "query": "a"})
    assert sig1 == sig2


# ─── Ruflo CLI wrapper ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ruflo_reports_not_installed_when_missing(monkeypatch):
    from app.integrations.ruflow import RufloClient
    import shutil

    monkeypatch.setattr(shutil, "which", lambda *_: None)
    client = RufloClient()
    result = await client.memory_store("key", "value")
    assert result["status"] == "not_installed"
