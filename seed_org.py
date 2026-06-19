"""Seed default org into Railway Postgres."""
import asyncio
from sqlalchemy import text
from app.database.session import async_session


async def seed():
    async with async_session() as db:
        # Check org
        r = await db.execute(
            text("SELECT id FROM organizations WHERE id = '00000000-0000-0000-0000-000000000001'")
        )
        if r.fetchone():
            print("Organization already exists")
        else:
            await db.execute(
                text("INSERT INTO organizations (id, name, slug, created_at, updated_at) "
                     "VALUES ('00000000-0000-0000-0000-000000000001', 'Default Org', "
                     "'default', NOW(), NOW())")
            )
            print("Created default organization")

        # Check user
        r = await db.execute(
            text("SELECT id FROM users WHERE id = '00000000-0000-0000-0000-000000000010'")
        )
        if r.fetchone():
            print("Dev user already exists")
        else:
            await db.execute(
                text("INSERT INTO users (id, email, name, clerk_id, created_at, updated_at) "
                     "VALUES ('00000000-0000-0000-0000-000000000010', 'dev@localhost', "
                     "'Developer', '00000000-0000-0000-0000-000000000010', NOW(), NOW())")
            )
            print("Created dev user")

        # Counts
        r = await db.execute(text("SELECT count(*) FROM organizations"))
        print(f"Organizations: {r.scalar()}")
        r = await db.execute(text("SELECT count(*) FROM leads"))
        print(f"Leads: {r.scalar()}")

        await db.commit()
        print("Seed complete!")


asyncio.run(seed())
