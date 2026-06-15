"""
Database seeder for Philosopher OS.

Creates sample data for development and testing.
Usage: python -m app.scripts.seed              # Shows warning
       python -m app.scripts.seed --force       # Actually seeds
       python -m app.scripts.seed --undo        # Removes all seed data
"""

import argparse
import asyncio
import uuid
import logging
from datetime import datetime, timedelta, date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.config import settings
from app.database.models import (
    Base,
    Organization,
    User,
    OrgMember,
    Lead,
    Client,
    Conversation,
    Message,
    Campaign,
    CampaignLead,
    Invoice,
    Expense,
    RevenueEvent,
    AgentMemory,
    KnowledgeBase,
    Task,
    CalendarEvent,
    AutomationRule,
    ScheduledJob,
    Notification,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("seed")

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")
MANAGER_ID = uuid.UUID("00000000-0000-0000-0000-000000000011")


async def seed() -> None:
    from app.database.session import DATABASE_URL
    engine = create_async_engine(DATABASE_URL)

    # Ensure schema exists
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Tables verified")

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        _ = session  # silence unused warning

        # ── 1. Organization ─────────────────────────────────
        org = Organization(
            id=ORG_ID,
            name="Socrates AI Agency",
            slug="socrates-ai",
            settings={"timezone": "UTC", "currency": "USD", "industry": "AI Services"},
        )
        session.add(org)

        # ── 2. Users ────────────────────────────────────────
        admin = User(
            id=ADMIN_ID,
            clerk_id="clerk_admin_001",
            email="admin@socrates.ai",
            name="Plato (Admin)",
            role="owner",
            preferences={"theme": "light", "notifications": True},
        )
        manager = User(
            id=MANAGER_ID,
            clerk_id="clerk_manager_001",
            email="manager@socrates.ai",
            name="Socrates (Manager)",
            role="admin",
            preferences={"theme": "light", "notifications": True},
        )
        session.add_all([admin, manager])

        # ── 3. Org Membership ───────────────────────────────
        session.add(OrgMember(org_id=ORG_ID, user_id=ADMIN_ID, role="owner", permissions=["*"]))
        session.add(OrgMember(org_id=ORG_ID, user_id=MANAGER_ID, role="admin", permissions=["read", "write"]))

        # ── 4. Leads ────────────────────────────────────────
        leads = [
            Lead(id=uuid.uuid4(), org_id=ORG_ID, name="Alice Johnson", email="alice@techcorp.com",
                 company="TechCorp", industry="SaaS", source="website", status="new", score=85,
                 tags=["enterprise", "tech"], notes="Interested in AI automation platform"),
            Lead(id=uuid.uuid4(), org_id=ORG_ID, name="Bob Smith", email="bob@healthai.io",
                 company="HealthAI", industry="Healthcare", source="referral", status="contacted", score=72,
                 tags=["healthcare", "mid-market"], notes="Referred by existing client"),
            Lead(id=uuid.uuid4(), org_id=ORG_ID, name="Carol Davis", email="carol@financehub.com",
                 company="FinanceHub", industry="Fintech", source="linkedin", status="qualified", score=91,
                 tags=["fintech", "high-value"], notes="VP of Engineering — decision maker"),
            Lead(id=uuid.uuid4(), org_id=ORG_ID, name="Dan Wilson", email="dan@retailplus.io",
                 company="RetailPlus", industry="E-commerce", source="conference", status="proposal", score=78,
                 tags=["ecommerce", "growth"], notes="Sent proposal on 6/1"),
            Lead(id=uuid.uuid4(), org_id=ORG_ID, name="Eve Martinez", email="eve@legalassist.com",
                 company="LegalAssist", industry="Legal", source="website", status="negotiation", score=88,
                 tags=["legal", "enterprise"], notes="Negotiating contract terms"),
            Lead(id=uuid.uuid4(), org_id=ORG_ID, name="Frank Lee", email="frank@edustart.org",
                 company="EduStart", industry="Education", source="cold_outreach", status="new", score=45,
                 tags=["education", "startup"], notes="Early stage — needs education"),
            Lead(id=uuid.uuid4(), org_id=ORG_ID, name="Grace Kim", email="grace@greenenergy.co",
                 company="GreenEnergy", industry="Energy", source="referral", status="contacted", score=65,
                 tags=["energy", "sustainability"], notes="Follow up next week"),
            Lead(id=uuid.uuid4(), org_id=ORG_ID, name="Henry Brown", email="henry@manufacturecorp.com",
                 company="ManufactureCorp", industry="Manufacturing", source="website", status="new", score=38,
                 tags=["manufacturing", "legacy"], notes="Initial inquiry only"),
            Lead(id=uuid.uuid4(), org_id=ORG_ID, name="Iris Chen", email="iris@meddevice.io",
                 company="MedDevice", industry="Medical", source="conference", status="won", score=95,
                 tags=["medical", "converted"], notes="Signed contract 5/28"),
            Lead(id=uuid.uuid4(), org_id=ORG_ID, name="Jack Turner", email="jack@logisticspro.com",
                 company="LogisticsPro", industry="Logistics", source="linkedin", status="lost", score=30,
                 tags=["logistics", "price-sensitive"], notes="Chose competitor"),
        ]
        session.add_all(leads)

        # ── 5. Clients (converted from leads) ──────────────
        clients = [
            Client(id=uuid.uuid4(), org_id=ORG_ID, lead_id=leads[8].id, name="Iris Chen",
                   email="iris@meddevice.io", company="MedDevice", industry="Medical",
                   contract_status="active", mrr=8500.0, lifetime_value=51000.0),
            Client(id=uuid.uuid4(), org_id=ORG_ID, name="Acme Ventures",
                   email="hello@acmeventures.com", company="Acme Ventures", industry="SaaS",
                   contract_status="active", mrr=12000.0, lifetime_value=72000.0),
            Client(id=uuid.uuid4(), org_id=ORG_ID, name="NorthStar Consulting",
                   email="info@northstar.com", company="NorthStar Consulting", industry="Consulting",
                   contract_status="active", mrr=5000.0, lifetime_value=30000.0),
        ]
        session.add_all(clients)

        # ── 6. Campaigns ────────────────────────────────────
        campaigns = [
            Campaign(id=uuid.uuid4(), org_id=ORG_ID, name="Q2 WhatsApp Outreach",
                     channel="whatsapp", industry="SaaS",
                     message_template="Hi {{name}}, we noticed your interest in AI automation...",
                     status="active", schedule_config={"time": "10:00", "timezone": "UTC"},
                     target_count=500, sent_count=320, reply_count=45, conversion_count=8),
            Campaign(id=uuid.uuid4(), org_id=ORG_ID, name="June Email Drip",
                     channel="email", industry="E-commerce",
                     message_template="Subject: Transform your customer engagement...",
                     status="draft", schedule_config={"cron": "0 9 * * 1", "timezone": "UTC"},
                     target_count=1000, sent_count=0, reply_count=0, conversion_count=0),
        ]
        session.add_all(campaigns)

        # ── 7. Campaign Leads ──────────────────────────────
        session.add(CampaignLead(campaign_id=campaigns[0].id, lead_id=leads[0].id, status="sent", sent_at=datetime.utcnow()))
        session.add(CampaignLead(campaign_id=campaigns[0].id, lead_id=leads[1].id, status="replied", sent_at=datetime.utcnow(), replied_at=datetime.utcnow()))

        # ── 8. Conversations & Messages ────────────────────
        conv1_id = uuid.uuid4()
        conv2_id = uuid.uuid4()
        conversations = [
            Conversation(id=conv1_id, org_id=ORG_ID, lead_id=leads[0].id, channel="whatsapp", status="active", last_message_at=datetime.utcnow()),
            Conversation(id=conv2_id, org_id=ORG_ID, client_id=clients[0].id, channel="email", status="active", last_message_at=datetime.utcnow()),
            Conversation(id=uuid.uuid4(), org_id=ORG_ID, lead_id=leads[2].id, channel="web", status="active", last_message_at=datetime.utcnow()),
        ]
        session.add_all(conversations)

        now = datetime.utcnow()
        messages = [
            Message(id=uuid.uuid4(), conversation_id=conv1_id, sender_type="lead", direction="inbound", body="Hi! I'm interested in learning more about your platform.", created_at=now - timedelta(hours=2)),
            Message(id=uuid.uuid4(), conversation_id=conv1_id, sender_type="agent", direction="outbound", body="Thanks for reaching out! I'd love to give you a demo. Are you free this week?", created_at=now - timedelta(hours=1)),
            Message(id=uuid.uuid4(), conversation_id=conv1_id, sender_type="lead", direction="inbound", body="Yes, Thursday at 2pm works for me.", created_at=now),
            Message(id=uuid.uuid4(), conversation_id=conv2_id, sender_type="client", direction="inbound", body="The monthly report looks great. Can we discuss scaling?", created_at=now),
        ]
        session.add_all(messages)

        # ── 9. Finance ──────────────────────────────────────
        invoices = [
            Invoice(id=uuid.uuid4(), org_id=ORG_ID, client_id=clients[1].id, invoice_number="INV-2026-001", amount=12000.0, currency="USD", status="paid", due_date=date(2026, 5, 15), paid_at=datetime(2026, 5, 10)),
            Invoice(id=uuid.uuid4(), org_id=ORG_ID, client_id=clients[0].id, invoice_number="INV-2026-002", amount=8500.0, currency="USD", status="overdue", due_date=date(2026, 5, 1)),
        ]
        session.add_all(invoices)

        expenses = [
            Expense(id=uuid.uuid4(), org_id=ORG_ID, category="SaaS", amount=1200.0, description="Anthropic API credits", incurred_at=date(2026, 5, 1)),
            Expense(id=uuid.uuid4(), org_id=ORG_ID, category="Marketing", amount=3000.0, description="LinkedIn Ads — Q2", incurred_at=date(2026, 5, 15)),
            Expense(id=uuid.uuid4(), org_id=ORG_ID, category="Office", amount=800.0, description="Coworking space", incurred_at=date(2026, 5, 1)),
        ]
        session.add_all(expenses)

        revenue = [
            RevenueEvent(id=uuid.uuid4(), org_id=ORG_ID, client_id=clients[1].id, invoice_id=invoices[0].id, amount=12000.0, type="mrr", period_start=date(2026, 5, 1), period_end=date(2026, 5, 31)),
            RevenueEvent(id=uuid.uuid4(), org_id=ORG_ID, client_id=clients[0].id, amount=8500.0, type="mrr", period_start=date(2026, 5, 1), period_end=date(2026, 5, 31)),
        ]
        session.add_all(revenue)

        # ── 10. Knowledge Base ─────────────────────────────
        knowledge = [
            KnowledgeBase(id=uuid.uuid4(), org_id=ORG_ID, title="Client Onboarding SOP",
                          content="Step-by-step guide for onboarding new clients...\n1. Send welcome email\n2. Schedule kickoff call\n3. Set up dashboard access\n4. Define KPIs",
                          category="operations", tags=["sop", "onboarding"]),
            KnowledgeBase(id=uuid.uuid4(), org_id=ORG_ID, title="Pricing Tiers 2026",
                          content="Standard: $5K/mo — Up to 500 leads, email + WhatsApp\nProfessional: $12K/mo — Up to 2000 leads, all channels\nEnterprise: Custom — Unlimited, dedicated agent",
                          category="sales", tags=["pricing", "sales"]),
            KnowledgeBase(id=uuid.uuid4(), org_id=ORG_ID, title="Common Lead FAQs",
                          content="Q: What channels do you support?\nA: WhatsApp, Email, SMS, and Web chat.\nQ: How long does setup take?\nA: Typically 2-3 business days.",
                          category="support", tags=["faq", "leads"]),
        ]
        session.add_all(knowledge)

        # ── 11. Agent Memories ─────────────────────────────
        agents = ["plato", "socrates", "aristotle", "athena", "heraclitus", "pythagoras", "solon", "leonidas", "archimedes", "odysseus"]
        for agent in agents:
            session.add(AgentMemory(
                id=uuid.uuid4(), org_id=ORG_ID, agent_name=agent,
                memory_type="insight", content=f"{agent.title()} processed 15 actions today.",
                importance=0.6,
            ))

        # ── 12. Tasks ──────────────────────────────────────
        tasks = [
            Task(id=uuid.uuid4(), org_id=ORG_ID, title="Follow up with Alice Johnson (TechCorp)",
                 description="She was interested in a demo last week", priority="high",
                 status="pending", due_date=datetime.utcnow() + timedelta(days=1)),
            Task(id=uuid.uuid4(), org_id=ORG_ID, title="Prepare Q2 Board Deck",
                 description="Include MRR growth, lead conversion, and campaign performance",
                 priority="high", status="in_progress", due_date=datetime.utcnow() + timedelta(days=3)),
            Task(id=uuid.uuid4(), org_id=ORG_ID, title="Review campaign performance",
                 description="Check Q2 WhatsApp outreach metrics", priority="medium",
                 status="pending", due_date=datetime.utcnow() + timedelta(days=5)),
            Task(id=uuid.uuid4(), org_id=ORG_ID, title="Update pricing page",
                 description="Add new enterprise tier and case studies", priority="low",
                 status="pending", due_date=datetime.utcnow() + timedelta(days=14)),
            Task(id=uuid.uuid4(), org_id=ORG_ID, title="Send welcome email to new leads",
                 description="Automation: trigger on lead creation", priority="medium",
                 status="completed", completed_at=datetime.utcnow()),
        ]
        session.add_all(tasks)

        # ── 13. Calendar Events ────────────────────────────
        events = [
            CalendarEvent(id=uuid.uuid4(), org_id=ORG_ID, title="Team Standup", event_type="meeting",
                          start_time=datetime.utcnow() + timedelta(hours=9), end_time=datetime.utcnow() + timedelta(hours=9, minutes=30)),
            CalendarEvent(id=uuid.uuid4(), org_id=ORG_ID, title="Client Call — TechCorp", event_type="client",
                          start_time=datetime.utcnow() + timedelta(days=1, hours=14), end_time=datetime.utcnow() + timedelta(days=1, hours=14, minutes=45)),
            CalendarEvent(id=uuid.uuid4(), org_id=ORG_ID, title="Q2 Board Meeting", event_type="meeting",
                          start_time=datetime.utcnow() + timedelta(days=7), end_time=datetime.utcnow() + timedelta(days=7, hours=2)),
        ]
        session.add_all(events)

        # ── 14. Automation Rules ───────────────────────────
        rules = [
            AutomationRule(id=uuid.uuid4(), org_id=ORG_ID, name="Lead Follow-up Reminder",
                           trigger_event="lead.created", conditions={"delay_hours": 24},
                           actions=[{"type": "assign_task", "assignee_agent": "odysseus"}]),
            AutomationRule(id=uuid.uuid4(), org_id=ORG_ID, name="Overdue Invoice Alert",
                           trigger_event="invoice.overdue", conditions={},
                           actions=[{"type": "send_email", "template": "payment_reminder"}]),
        ]
        session.add_all(rules)

        # ── 15. Notifications ──────────────────────────────
        notifications = [
            Notification(id=uuid.uuid4(), org_id=ORG_ID, user_id=ADMIN_ID, type="lead",
                         title="New lead from website", body="Frank Lee from EduStart submitted a contact form", read=False),
            Notification(id=uuid.uuid4(), org_id=ORG_ID, user_id=ADMIN_ID, type="invoice",
                         title="Invoice overdue", body="INV-2026-002 for MedDevice is overdue", read=False),
        ]
        session.add_all(notifications)

        # ── Commit ─────────────────────────────────────────
        await session.commit()

    counts = {
        "organizations": 1,
        "users": 2,
        "leads": len(leads),
        "clients": len(clients),
        "campaigns": len(campaigns),
        "conversations": len(conversations),
        "messages": len(messages),
        "invoices": len(invoices),
        "expenses": len(expenses),
        "revenue_events": len(revenue),
        "knowledge_articles": len(knowledge),
        "agent_memories": len(agents),
        "tasks": len(tasks),
        "calendar_events": len(events),
        "automation_rules": len(rules),
        "notifications": len(notifications),
    }
    logger.info("Seeding complete:")
    for name, count in counts.items():
        logger.info(f"  {name}: {count}")

    await engine.dispose()


async def undo_seed() -> None:
    """Remove all seed data by known IDs."""
    from app.database.session import DATABASE_URL
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        tables = [
            "notifications", "scheduled_jobs", "audit_logs", "automation_rules",
            "agent_memories", "knowledge_base", "calendar_events", "tasks",
            "revenue_events", "expenses", "invoices", "campaign_leads",
            "messages", "conversations", "campaigns", "clients", "leads",
            "org_members", "users", "organizations",
        ]
        for table in tables:
            await session.execute(text(f"DELETE FROM {table} WHERE org_id = '{ORG_ID}' OR id = '{ORG_ID}'"))
        # Also delete users specifically
        for uid in [ADMIN_ID, MANAGER_ID]:
            await session.execute(text(f"DELETE FROM users WHERE id = '{uid}'"))
        await session.commit()
        logger.info(f"Seed data removed — all tables cleaned for org {ORG_ID}")

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Philosopher OS database seeder")
    parser.add_argument("--force", action="store_true", help="Actually seed the database")
    parser.add_argument("--undo", action="store_true", help="Delete all seed data")
    args = parser.parse_args()

    if args.force:
        asyncio.run(seed())
    elif args.undo:
        asyncio.run(undo_seed())
    else:
        print("⚠️  Seed data is NOT auto-applied. Run with --force to seed.")
        print("   python -m app.scripts.seed --force")
        print("   python -m app.scripts.seed --undo  (removes seed data)")
