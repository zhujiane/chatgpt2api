from __future__ import annotations

import os

import uvicorn
from api import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8001")),
        access_log=False,
        log_level="info",
    )
