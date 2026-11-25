from fastapi import FastAPI, Request

app = FastAPI()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/")
async def response(request: Request) -> dict[str, str]:
    return {
        "served_by": "remote-backend",
        "policy_header": request.headers.get("x-demo-policy", ""),
    }
