import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = FastAPI(title="PT Generator", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local dev only
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.web.server:app", host="0.0.0.0", port=8000, reload=True)
