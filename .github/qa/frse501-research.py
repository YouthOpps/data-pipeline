"""Read only the current official contest catalogue and the discovered award overviews."""
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.parse

sys.path.insert(0,"/src/adapters/pl-frse")
import adapter as a

# Guard the physical opener as well as application discovery. A redirect to a
# different publisher URL must not expand this bounded maintenance job.
approved = set()
open_wire = a._OPENER.open
def scoped_open(request, *args, **kwargs):
    parsed = urllib.parse.urlsplit(request.full_url)
    if parsed.hostname in a._PUBLISHER_HOSTS and request.full_url not in approved:
        raise a.AdapterError("Targeted research redirect outside accepted routes", "access")
    return open_wire(request, *args, **kwargs)
a._OPENER.open = scoped_open

def approve(url):
    parsed = urllib.parse.urlsplit(url)
    assert parsed.netloc in {"www.frse.org.pl","eduinspiracje.org.pl"}
    approved.update({url, "https://"+parsed.netloc+"/robots.txt"})

facts = []
def record(item):
    facts.append(item)
    Path("/ram/facts.json").write_text(json.dumps(facts,ensure_ascii=False))

with a.execution(), a.phase("prepublication",800):
    a.prepare_collection()
    catalogue_urls=set()
    pending, visited = {1}, set()
    while pending:
        page = min(pending)
        pending.remove(page)
        visited.add(page)
        url = a.listing_url("konkursy",page)
        approve(url)
        root = a.fetch_inventory_page(lambda u,**kw:a.read_html(u,**kw),url)
        pages = a.page_numbers(root)
        assert pages <= {1,2}
        pending.update(pages - visited)
        for card in a.nodes(a.one(a.nodes(root,"main"),"main"),class_name="desc_holder"):
            title = a.one(a.nodes(card,"h2"),"contest title").text()
            links = a.material_links(card,url)
            if not any(urllib.parse.urlsplit(link).hostname=="eduinspiracje.org.pl" for link in links):
                continue
            record({"type":"catalogue","page":page,"title":title,"links":links})
            catalogue_urls.update(links)
    for path in ("eduinspiracje","eduinspirator","eduinspiracje-media"):
        url=f"https://eduinspiracje.org.pl/{path}/o-konkursie"
        if url not in catalogue_urls:
            record({"type":"not_advertised","url":url})
            continue
        approve(url)
        doc = a.read_html(url)
        content=a.detail_content(doc)
        parts = [n.text() for n in content.walk() if n.tag in ("p","li","h1","h2","h3","h4","td")]
        pattern = re.compile(r"samodzielnego zgłoszenia|nominac|kandydatur|"
                             r"zgłoszenia do|termin zgłosze|edycj[iaę] 202[4-8]", re.I)
        evidence = []
        for paragraph in parts:
            match = pattern.search(paragraph)
            if match and "@" not in paragraph:
                start=max(0, match.start()-40)
                evidence.append(paragraph[start:start+125])
            if len(evidence) >= 3: break
        links=[]
        for n in a.nodes(content,"a"):
            if "href" not in n.attrs: continue
            target=a.absolute_link(url,n.attrs["href"])
            parsed=urllib.parse.urlsplit(target)
            if parsed.scheme not in ("http","https") or parsed.query or parsed.username:
                continue
            if not re.search(r"regulamin|zasady|zgłosz|nominac|edycj|202[4-8]|\.pdf",
                             n.text()+" "+parsed.path,re.I): continue
            links.append({"text":n.text()[:80],"url":target})
        record({"type":"overview","url":url,"nomination_only":bool(a._NOMINATION_ONLY.search(content.text())),
                "evidence":evidence,"links":links,"body_sha256":hashlib.sha256(content.text().encode()).hexdigest()})
print("Completed genuine public catalogue and overview research", len(facts), "items.")
