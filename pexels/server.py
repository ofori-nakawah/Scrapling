"""HTTP endpoint over the Pexels crawler: pass a search query, get photos with descriptions.

    docker run -d --name pexels-api -p 127.0.0.1:8090:8000 -v "$PWD:/work" \
        --entrypoint uv scrapling:stealth run python /work/server.py

    GET /search?q=nature                 first page (24 photos)
    GET /search?q=nature&page=3          a later page (1-20, robots.txt)
    GET /search?q=nature&pages=3         pages 1-3 merged (max 5 per request)
    GET /search?q=nature&size=original   image_url at full resolution (medium|large|original)
    GET /health

    GET /image/<id>                      one photo as a JPEG file (size=medium|large|original)
    GET /download?q=nature               the search as a zip: images/ + photos.json + photos.csv
                                         (same q/page/pages/size; limit=N keeps the first N)

/search does not download anything: image_url / thumb_url point at Pexels' CDN.
/image and /download fetch from that CDN (never the robots-disallowed /download/ path).
One stealth browser serves every request, from a single worker thread (the
Playwright sync API is bound to the thread that started it), so requests queue.
"""
import csv, io, json, os, queue, re, sys, threading, time, zipfile
from concurrent.futures import Future, ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scrapling.fetchers import Fetcher, StealthySession
from crawl import MAX_PAGE, SIZES, page_photos, record

PORT = int(os.environ.get("PORT", 8000))
MAX_PAGES_PER_REQUEST = 5
CACHE_TTL = 3600
MAX_ZIP_PHOTOS = {"medium": 120, "large": 120, "original": 24}   # originals run 5-15 MB each
DELAY = 1.5                       # seconds between search pages fetched for one request

jobs = queue.Queue()
cache, cache_lock = {}, threading.Lock()


def worker():
    """Owns the browser. Each job is (query, page, Future); the result is (photos, pagination)."""
    session = None
    while True:
        query, page, fut = jobs.get()
        for attempt in (1, 2):
            try:
                if session is None:
                    session = StealthySession(headless=True, network_idle=True, timeout=90000)
                    session.__enter__()
                fut.set_result(page_photos(session, query, page))
                break
            except Exception as e:
                # A crashed or wedged browser is replaced once before the request fails.
                try:
                    session and session.__exit__(None, None, None)
                except Exception:
                    pass
                session = None
                if attempt == 2:
                    fut.set_exception(e)


def fetch_page(query, page):
    key = (query.lower(), page)
    with cache_lock:
        hit = cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1], True
    fut = Future()
    jobs.put((query, page, fut))
    result = fut.result(timeout=180)
    with cache_lock:
        cache[key] = (time.time(), result)
    return result, False


def photo(a, size):
    r = record(a, size)
    del r["file"]                 # crawler-only: where the downloader saves it
    return r


def search(params):
    q = (params.get("q") or [""])[0].strip()
    if not q:
        return 400, {"error": "missing ?q= search query"}
    size = (params.get("size") or ["large"])[0]
    if size not in SIZES:
        return 400, {"error": f"size must be one of {', '.join(SIZES)}"}
    try:
        start = int((params.get("page") or ["1"])[0])
        count = int((params.get("pages") or ["1"])[0])
    except ValueError:
        return 400, {"error": "page and pages must be integers"}
    if not 1 <= start <= MAX_PAGE:
        return 400, {"error": f"page must be 1-{MAX_PAGE} (Pexels robots.txt disallows page 21+)"}
    if not 1 <= count <= MAX_PAGES_PER_REQUEST:
        return 400, {"error": f"pages must be 1-{MAX_PAGES_PER_REQUEST}"}

    photos, seen, cached, pag = [], set(), True, None
    for page in range(start, min(start + count, MAX_PAGE + 1)):
        if pag and page > pag["total_pages"]:
            break
        if page > start and not cached:
            time.sleep(DELAY)
        (items, pag), cached = fetch_page(q, page)
        for a in items:
            if a["id"] not in seen:
                seen.add(a["id"]); photos.append(photo(a, size))
    last = min(start + count - 1, pag["total_pages"], MAX_PAGE)
    return 200, {"query": q, "size": size, "pages": [start, last], "count": len(photos),
                 "total_results": pag["total_results"],
                 "next_page": last + 1 if last < min(pag["total_pages"], MAX_PAGE) else None,
                 "license": "https://www.pexels.com/license/", "photos": photos}


def cdn_url(photo_id, size):
    base = f"https://images.pexels.com/photos/{photo_id}/pexels-photo-{photo_id}.jpeg"
    # fm=jpg: auto=compress would otherwise negotiate AVIF from our Accept header.
    return {"medium": f"{base}?auto=compress&cs=tinysrgb&w=750&fm=jpg",
            "large": f"{base}?auto=compress&cs=tinysrgb&w=1440&fm=jpg",
            "original": f"{base}?cs=srgb&fm=jpg"}[size]


def fetch_image(url):
    for attempt in range(3):
        try:
            r = Fetcher.get(url, timeout=90)
            if r.status == 200 and r.body:
                return r.body
            if r.status == 404:
                return None
        except Exception:
            pass
        time.sleep(1 + attempt)
    raise RuntimeError(f"could not fetch {url}")


def image(photo_id, params):
    size = (params.get("size") or ["large"])[0]
    if size not in SIZES:
        return 400, {"error": f"size must be one of {', '.join(SIZES)}"}
    body = fetch_image(cdn_url(photo_id, size))
    if body is None:
        return 404, {"error": f"no Pexels photo {photo_id}"}
    return 200, ("image/jpeg", f"pexels-{photo_id}-{size}.jpg", body)


def download(params):
    status, res = search(params)
    if status != 200:
        return status, res
    size = res["size"]
    try:
        limit = int((params.get("limit") or [MAX_ZIP_PHOTOS[size]])[0])
    except ValueError:
        return 400, {"error": "limit must be an integer"}
    if not 1 <= limit <= MAX_ZIP_PHOTOS[size]:
        return 400, {"error": f"limit must be 1-{MAX_ZIP_PHOTOS[size]} for size={size}"}
    photos = res["photos"][:limit]
    with ThreadPoolExecutor(4) as ex:
        bodies = list(ex.map(lambda p: _try(fetch_image, p["image_url"]), photos))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:      # JPEGs don't compress further
        for p, body in zip(photos, bodies):
            p["file"] = f"images/{p['id']}.jpg" if body else None
            if body:
                z.writestr(p["file"], body)
        meta = {k: v for k, v in res.items() if k != "photos"} | {"count": sum(1 for b in bodies if b), "photos": photos}
        z.writestr("photos.json", json.dumps(meta, ensure_ascii=False, indent=1))
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=list(photos[0])); w.writeheader(); w.writerows(photos)
        z.writestr("photos.csv", out.getvalue())
    slug = re.sub(r"[^a-z0-9]+", "-", res["query"].lower()).strip("-") or "pexels"
    return 200, ("application/zip", f"pexels-{slug}-p{res['pages'][0]}-{res['pages'][1]}-{size}.zip", buf.getvalue())


def _try(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        params = parse_qs(u.query)
        try:
            if u.path == "/health":
                status, body = 200, {"ok": True, "queued": jobs.qsize(), "cached_pages": len(cache)}
            elif u.path == "/search":
                status, body = search(params)
            elif u.path == "/download":
                status, body = download(params)
            elif m := re.fullmatch(r"/image/(\d+)(?:\.jpg)?", u.path):
                status, body = image(m.group(1), params)
            else:
                status, body = 404, {"error": "try GET /search?q=<query>, /download?q=<query> or /image/<id>"}
        except Exception as e:
            status, body = 502, {"error": f"fetching from Pexels failed: {e}"}
        if isinstance(body, tuple):                 # a file: (content type, filename, bytes)
            ctype, name, data = body
        else:
            ctype, name, data = "application/json; charset=utf-8", None, json.dumps(body, ensure_ascii=False, indent=1).encode()
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(data)))
        if name:
            self.send_header("content-disposition", f'attachment; filename="{name}"')
        self.send_header("access-control-allow-origin", "*")
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True).start()
    print(f"listening on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
