"""
AI Revenue Recovery — FastAPI Application

Main entry point. Provides:
- REST API endpoints for the Streamlit dashboard
- Health check
- Recovery batch processing trigger
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database.db import init_db

app = FastAPI(
    title="AI Revenue Recovery",
    description="AI-powered failed subscription payment recovery agent",
    version="1.0.0",
)

# Allow Streamlit dashboard to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    """Initialize database on app startup."""
    init_db()


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "ai-revenue-recovery",
    }


from api.routes import router as api_router
app.include_router(api_router, prefix="/api")


if __name__ == "__main__":
    import uvicorn
    import config

    uvicorn.run(
        "main:app",
        host=config.API_HOST,
        port=config.API_PORT,
        reload=True,
    )
