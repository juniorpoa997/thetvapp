import base64
import time
import json
import os
import re
from fastapi import Request, Response, Cookie
from fastapi.responses import RedirectResponse
from request_helper import Requester
from typing import Annotated

URI_MATCH = re.compile(r"URI=\"([^\"]+)\"")

async def cors(request: Request, origins, method="GET") -> Response:
    global URI_MATCH
    current_domain = request.headers.get("origin")
    if current_domain is None:
        current_domain = origins
    if current_domain not in origins.replace(", ", ",").split(",") and origins != "*":
        return Response("Bad domain!", status_code=404)
    if not request.query_params.get('url'):
        return Response("No url passed!", status_code=404)
    file_type = request.query_params.get('type')
    requested = Requester(str(request.url))
    # Assegure-se de que o esquema seja HTTPS
    main_url = "https://" + requested.host + requested.path + "?url="
    key_url = main_url.replace("/cors", "/key")
    referer = requested.base_headers.get("referer")
    if not referer:
        return Response("No referrer passed!", status_code=404)
    url = requested.query_params.get("url")
    url += "?" + requested.query_string(requested.remaining_params)
    requested = Requester(url)
    hdrs = request.headers.mutablecopy()
    hdrs["Accept-Encoding"] = ""
    hdrs.update(json.loads(request.query_params.get("headers", "{}").replace("'", '"')))
    content, headers, code, cookies = requested.get(
        data=None,
        headers=hdrs,
        cookies=request.cookies,
        method=request.query_params.get("method", method),
        json_data=json.loads(request.query_params.get("json", "{}")),
        additional_params=json.loads(request.get('params', '{}'))
    )
    headers['Access-Control-Allow-Origin'] = current_domain
    # Remover cabeçalhos desnecessários
    del_keys = [
        'Vary',
        'Content-Encoding',
        'Transfer-Encoding',
        'Content-Length',
    ]
    for key in del_keys:
        headers.pop(key, None)

    if (file_type == "m3u8" or ".m3u8" in url) and code != 404:
        content = content.decode("utf-8")
        new_content = ""
        for line in content.split("\n"):
            if line.startswith("#EXT-X-KEY"):
                uri_match = URI_MATCH.search(line)
                if not uri_match:
                    print("No uri in key def")
                    continue
                new_content += URI_MATCH.sub(lambda x: f"URI=\"{key_url+requested.safe_sub(x.group(1))+f'&referer={referer}'}\"", line)
            elif line.startswith("#"):
                new_content += line
            elif line.startswith('/'):
                new_content += main_url + requested.safe_sub(requested.host + line)
            elif line.startswith('http'):
                new_content += main_url + requested.safe_sub(line)
            elif line.strip(' '):
                if '.ts' in line and not os.getenv('proxy_ts', True):
                    new_content += "https://" + requested.host + '/'.join(str(requested.path).split('?')[0].split('/')[:-1]) + '/' + line
                else:
                    new_content += main_url + requested.safe_sub(
                        requested.host +
                        '/'.join(str(requested.path).split('?')[0].split('/')[:-1]) +
                        '/' +
                        requested.safe_sub(line) 
                    ) + f'&referer={referer}'
            new_content += "\n"
        content = new_content

    if "location" in headers:
        if headers["location"].startswith("/"):
            headers["location"] = "https://" + requested.host + headers["location"]
        headers["location"] = main_url + headers["location"] + f"&referer={referer}"
    
    # Adicione o cabeçalho Strict-Transport-Security
    headers['Strict-Transport-Security'] = 'max-age=63072000; includeSubDomains; preload'

    resp = Response(content, code, headers=headers)
    resp.set_cookie("_last_requested", requested.host, max_age=3600, httponly=True)
    return resp


def add_cors(app, origins, setup_with_no_url_param=False):
    cors_path = os.getenv('cors_url', '/cors')

    @app.get(cors_path)
    async def cors_caller(request: Request) -> Response:
        return await cors(request, origins=origins)

    @app.post(cors_path)
    async def cors_caller_post(request: Request) -> Response:
        return await cors(request, origins=origins, method="POST")

    if setup_with_no_url_param:
        @app.get("/{mistaken_relative:path}")
        async def cors_caller_for_relative(request: Request, mistaken_relative: str, _last_requested: Annotated[str, Cookie(...)]) -> RedirectResponse:
            x = Requester(str(request.url))
            x = x.query_string(x.query_params)
            resp = RedirectResponse(f"/cors?url={_last_requested}/{mistaken_relative}{'&' + x if x else ''}")
            return resp

        @app.post("/{mistaken_relative:path}")
        async def cors_caller_for_relative(request: Request, mistaken_relative: str,
                                           _last_requested: Annotated[str, Cookie(...)]) -> RedirectResponse:
            x = Requester(str(request.url))
            x = x.query_string(x.query_params)
            resp = RedirectResponse(f"/cors?url={_last_requested}/{mistaken_relative}{'&' + x if x else ''}")
            return resp


CURRENT_KEY = None
KEY_LAST_SET = None

async def keys(request, origins):
    global CURRENT_KEY
    global KEY_LAST_SET
    content = None
    headers = None
    current_domain = request.headers.get("origin")
    if current_domain is None:
        current_domain = origins
    if current_domain not in origins.replace(", ", ",").split(",") and origins != "*":
        return Response("Bad domain!", status_code=404)

    if CURRENT_KEY:
        now = time.time()
        diff = now - KEY_LAST_SET
        if diff < 600:  # 10 mins
            content = CURRENT_KEY

    if not content:
        requested = Requester(str(request.url))
        target_url = requested.query_params.get("url")
        referer_url = requested.query_params.get("referer")
        requested = Requester(referer_url)
        content, headers, code, cookies = requested.get(
            data=None,
            headers=request.headers,
            cookies=request.cookies,
            method="GET"
        )
        hdrs = request.headers.mutablecopy()
        del hdrs["cookie"]
        final_request = Requester(target_url)
        content, headers, code, cookies = final_request.get(
            data=None,
            headers=hdrs,
            cookies=cookies,
            method="GET"
        )
        CURRENT_KEY = content
        KEY_LAST_SET = time.time()
        del_keys = [
            'Vary',
            'Content-Encoding',
            'Transfer-Encoding',
            'Content-Length',
        ]
        
        for key in del_keys:
            headers.pop(key, None)

    if not CURRENT_KEY:
        return Response("Failed to get key!", status_code=500)

    if not headers:
        headers = {}
    headers['Access-Control-Allow-Origin'] = current_domain
    return Response(content=CURRENT_KEY, headers=headers, media_type="application/octet-stream")

def add_keys(app, origins, setup_with_no_url_param=False):
    key_path = os.getenv('key_url', '/key')

    @app.get(key_path)
    async def key_caller(request: Request) -> Response:
        return await keys(request, origins=origins)

    if setup_with_no_url_param:
        raise ValueError("Not implemented 'setup_with_no_url_param' for add_keys!")
