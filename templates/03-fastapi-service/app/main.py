"""
FastAPI application main entrypoint.
Includes Jinja2 templating setup.
"""
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

app = FastAPI(title="DockDuck API")
templates = Jinja2Templates(directory="app/templates")

@app.get("/")
async def root(request: Request):
    """Render the default index template."""
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "message": "DockDuck API is running!"}
    )