"""Seed default org/user for Railway Postgres."""
import asyncio
from sqlalchemy import text
from app.database.session import async_session


async def seed():
    async with async_session() as db:
        result = await db.execute(
            text("SELECT id FROM organizations WHERE id = '00000000-0000-0000-0000-000000000001'")
        )
        if result.fetchone():
            print("Organization already exists")
        else:
            await db.execute(
                text("INSERT INTO organizations (id, name, created_at, updated_at) "
                     "VALUES ('00000000-0000-0000-0000-000000000001', 'Default Org', NOW(), NOW())")
            )
            print("Created default organization")

        result = await db.execute(
            text("SELECT id FROM users WHERE id = '00000000-0000-0000-0000-000000000010'")
        )
        if result.fetchone():
            print("Dev user already exists")
        else:
            await db.execute(
                text("INSERT INTO users (id, email, name, clerk_id, created_at, updated_at) "
                     "VALUES ('00000000-0000-0000-0000-000000000010', 'dev@localhost', "
                     "'Developer', '00000000-0000-0000-0000-000000000010', NOW(), NOW())")
            )
            print("Created dev user")

        # Count
        r = await db.execute(text("SELECT count(*) FROM organizations"))
        print(f"Organizations: {r.scalar()}")
        r = await db.execute(text("SELECT count(*) FROM leads"))
        print(f"Leads: {r.scalar()}")

        await db.commit()
        print("Seed complete!")


asyncio.run(seed())
