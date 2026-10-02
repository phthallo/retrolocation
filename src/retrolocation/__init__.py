import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "retrolocation.main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
    )
