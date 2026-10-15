"""Crawl Pexels search results with Scrapling and download the photos.

    docker run --rm -v "$PWD:/work" --entrypoint uv scrapling:stealth run python /work/crawl.py "nature" --pages 3 --size large

Search pages are fetched with StealthyFetcher (pexels.com is behind Cloudflare);
the photo list is read from the page's __NEXT_DATA__ JSON, 24 photos a page.
Respects robots.txt: pages 1-20 only, and never the /download/ path - images
come straight from the images.pexels.com CDN.
Writes out/<query>/{images/, photos.csv, photos.json, index.html}.
"""
import argparse, csv, html, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from scrapling.fetchers import StealthySession, Fetcher

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_PAGE = 20                      # robots.txt disallows *page=21 and beyond
SIZES = ("medium", "large", "original")


def page_photos(session, query, page):
    url = f"https://www.pexels.com/search/{quote(query)}/" + (f"?page={page}" if page > 1 else "")
    p = session.fetch(url)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', p.html_content, re.S)
    if p.status != 200 or not m:
        raise RuntimeError(f"page {page}: HTTP {p.status}, no data")
    d = json.loads(m.group(1))["props"]["pageProps"]["initialData"]
    return [x["attributes"] for x in d["data"] if x.get("type") == "photo"], d["pagination"]


def record(a, size):
    img = a["image"]
    u = a.get("user") or {}
    return {"id": a["id"], "title": a.get("title") or "", "description": a.get("description") or "",
            "width": a["width"], "height": a["height"],
            "photographer": " ".join(filter(None, (u.get("first_name"), u.get("last_name")))),
            "photographer_url": f"https://www.pexels.com/@{u['slug']}/" if u.get("slug") else "",
            "pexels_url": f"https://www.pexels.com/photo/{a.get('slug', 'photo')}-{a['id']}/",
            "tags": ", ".join(t["name"] for t in a.get("tags") or []),
            # auto=compress otherwise negotiates AVIF from our Accept header; fm=jpg pins JPEG.
            "image_url": img["download_link"] if size == "original" else img[size] + "&fm=jpg",
            "thumb_url": img["medium"], "file": f"images/{a['id']}.jpg"}


def download(rec, outdir):
    path = os.path.join(outdir, rec["file"])
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return "kept"
    for attempt in range(3):
        try:
            r = Fetcher.get(rec["image_url"], timeout=60)
            if r.status == 200 and r.body:
                open(path, "wb").write(r.body)
                return "ok"
        except Exception:
            pass
        time.sleep(2 * (attempt + 1))
    return "failed"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--pages", type=int, default=2, help=f"search pages, 24 photos each (max {MAX_PAGE})")
    ap.add_argument("--size", choices=SIZES, default="large", help="medium 750w, large 1440w, original full-res")
    ap.add_argument("--delay", type=float, default=2.0, help="seconds between search pages")
    a = ap.parse_args()
    pages = min(a.pages, MAX_PAGE)
    if a.pages > MAX_PAGE:
        print(f"capping at {MAX_PAGE} pages (robots.txt)", file=sys.stderr)

    slug = re.sub(r"[^a-z0-9]+", "-", a.query.lower()).strip("-")
    outdir = os.path.join(HERE, "out", slug)
    os.makedirs(os.path.join(outdir, "images"), exist_ok=True)

    recs, seen = [], set()
    with StealthySession(headless=True, network_idle=True, timeout=90000) as s:
        for page in range(1, pages + 1):
            photos, pg = page_photos(s, a.query, page)
            new = [record(p, a.size) for p in photos if p["id"] not in seen]
            seen.update(r["id"] for r in new); recs += new
            print(f"page {page}/{min(pages, pg['total_pages'])}: {len(new)} photos ({pg['total_results']} results in all)")
            if page >= pg["total_pages"]:
                break
            time.sleep(a.delay)

    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(lambda r: download(r, outdir), recs))
    for r, res in zip(recs, results):
        r["downloaded"] = res != "failed"

    json.dump(recs, open(os.path.join(outdir, "photos.json"), "w"), indent=1, ensure_ascii=False)
    with open(os.path.join(outdir, "photos.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(recs[0])); w.writeheader(); w.writerows(recs)
    gallery(os.path.join(outdir, "index.html"), a.query, recs)
    size_mb = sum(os.path.getsize(os.path.join(outdir, r["file"])) for r in recs if r["downloaded"]) / 1e6
    print(f"{results.count('ok')} downloaded, {results.count('kept')} already had, {results.count('failed')} failed; {size_mb:.0f} MB")
    print("wrote", outdir)


def gallery(path, query, recs):
    e = html.escape
    cards = "".join(
        f"<figure><a href='{e(r['file'])}' target=_blank><img loading=lazy src='{e(r['file'])}' alt='{e(r['title'])}'></a>"
        f"<figcaption>{e(r['title'] or 'Untitled')}<br><a href='{e(r['photographer_url'])}'>{e(r['photographer'])}</a> · "
        f"<a href='{e(r['pexels_url'])}'>Pexels</a> · {r['width']}×{r['height']}</figcaption></figure>"
        for r in recs if r["downloaded"])
    open(path, "w").write(f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Pexels: {e(query)}</title><style>body{{font:13px system-ui;margin:16px;background:#fafafa;color:#111}}
main{{columns:280px;column-gap:12px}}figure{{margin:0 0 12px;break-inside:avoid;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px #0002}}
img{{width:100%;display:block}}figcaption{{padding:8px;color:#555}}a{{color:#0a7d5a}}</style>
<h2>Pexels — “{e(query)}” ({sum(r['downloaded'] for r in recs)} photos)</h2>
<p>Photos by their credited photographers on Pexels, under the <a href="https://www.pexels.com/license/">Pexels license</a>.</p><main>{cards}</main>""")


if __name__ == "__main__":
    main()
