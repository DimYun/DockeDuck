from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

app = FastAPI(title="DockDuck Demo API")
templates = Jinja2Templates(directory="app/templates")

@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse(
        "index.html", 
        {"request": request, "message": "API Running in DockDuck Isolated Environment"}
    )