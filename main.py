import os
import logging
import uvicorn
import asyncio

from modules.tvapp import TheTvApp
from cors import add_cors, add_keys
from request_helper import Requester
from fastapi import FastAPI, Request
from rich.logging import RichHandler
from fastapi.responses import Response, RedirectResponse
from apscheduler.schedulers.background import BackgroundScheduler

try:
    enable_docs = bool(os.getenv("documentation", False))
    docs_url = os.getenv('docs_url', '/docs') if enable_docs and os.getenv('docs_url', '/docs') else None
    redoc_url = os.getenv('redoc_url', '/redoc') if enable_docs and os.getenv('redoc_url', '/redoc') else None
    # set environment variable 'documentation' to 'True' if you want to enable the /docs path
except TypeError:
    enable_docs = False
    docs_url = '/docs' if enable_docs else None
    redoc_url = '/redoc' if enable_docs else None

allow_no_url_param_also = os.getenv("no_url_param", "false")
allow_no_url_param_also = allow_no_url_param_also == "true"

app = FastAPI(openapi_url=None, docs_url=docs_url, redoc_url=redoc_url)
default_port = "5010"

rh = RichHandler(rich_tracebacks=True)
rh.setFormatter(logging.Formatter("%(message)s"))
rh.setLevel(os.getenv("loglevel", logging.DEBUG))
handlers = [rh]

if os.getenv("store_debug", False):
    fh = logging.FileHandler("debug.log", mode="w", encoding="utf-8")
    handlers.append(fh)

setup = logging.basicConfig(
    level="NOTSET",
    format="%(asctime)s %(levelname)s | %(name)s | %(message)s",
    datefmt="[%X]",
    handlers=handlers
)
logger = logging.getLogger("Main")

tva = None
def update_tva():
    global tva
    logger.debug("Updating tva...")
    tva = TheTvApp()

scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(func=update_tva, trigger="interval", seconds=900) # 15 mins
scheduler.start()
update_tva()

@app.get('/playlist')
async def get_channel(request: Request):
    while not tva:
        await asyncio.sleep(0.1)
    req = Requester(str(request.url))
    name = req.query_params.get("name")
    if not name:
        return Response("No name param!", status_code=404)
    route = req.query_params.get("route")
    if not route:
        logger.warn("No route passed with /playlist req...")
    name, task = await tva.scrape_channel(name, route)
    name, playlist = await task
    if req.query_params.get("redirect"):
        return RedirectResponse(playlist)
    return {name: playlist}


allowed_origins = os.getenv("origins", "*")
# You may set your environment variable with the domains you want to allow requests from(your site)
# You may put ',' between the domains if you have multiple domains

try:
    port = int(float(os.getenv("port", default_port)))
except TypeError:
    port = int(default_port)
# You don't need to change anything here unless you want to run it on a different or specific port
# to run on a different port you can set the port env variable

add_cors(app, allowed_origins, allow_no_url_param_also)
add_keys(app, allowed_origins, allow_no_url_param_also)

if __name__ == '__main__':
    uvicorn.run(app, host="localhost", port=port)
