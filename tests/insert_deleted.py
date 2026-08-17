import asyncio
import asyncpg
import datetime

async def add_deleted_comment():
    pool = await asyncpg.create_pool(
        'postgresql://postgres.bjzqrytdwsqsumrbkmkp:link%40nikplea@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres?pgbouncer=true',
        statement_cache_size=0
    )
    async with pool.acquire() as db:
        await db.execute(
            'INSERT INTO deleted_comments (comment_id, deleted_at) VALUES ($1, $2)',
            'cmt_video_demo', datetime.datetime.utcnow()
        )
        print('✅ Inserted one row into deleted_comments')
    await pool.close()

asyncio.run(add_deleted_comment())
