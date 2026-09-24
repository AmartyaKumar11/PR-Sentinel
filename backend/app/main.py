import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.config import settings
from app.database import close_db, get_db
from app.routes import health, reviews, stream, tasks, webhook

logging.basicConfig(level=settings.LOG_LEVEL.upper())


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_db()
    bot_task = None
    token = settings.DISCORD_BOT_TOKEN
    if token and "your_discord" not in token:
        from app.discord.bot import bot

        bot_task = asyncio.create_task(bot.start(token))
    yield
    if bot_task:
        from app.discord.bot import bot

        await bot.close()
        bot_task.cancel()
    await close_db()


app = FastAPI(title="PR Sentinel", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(webhook.router)
app.include_router(tasks.router)
app.include_router(reviews.router)
app.include_router(stream.router)


@app.get("/")
async def root():
    return RedirectResponse("/api/health")
