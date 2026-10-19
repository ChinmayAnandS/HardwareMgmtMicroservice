"""Database lifecycle and an overridable request dependency."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase


@asynccontextmanager
async def database_lifespan(app: FastAPI):
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    connection_string = os.getenv("MONGODB_CONN_STRING")
    if not connection_string:
        raise RuntimeError("Set MONGODB_CONN_STRING in backend/.env or the environment.")

    client = AsyncIOMotorClient(connection_string, serverSelectionTimeoutMS=5000)
    app.state.database = client[os.getenv("MONGODB_DATABASE", "hardwareManagement")]
    try:
        yield
    finally:
        client.close()


def get_database(request: Request) -> AsyncIOMotorDatabase:
    return request.app.state.database
