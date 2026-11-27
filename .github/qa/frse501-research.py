"""Read only the current official contest catalogue and the discovered award overviews."""
import json
from pathlib import Path
import re
import sys
import urllib.parse

sys.path.insert(0,"/src/adapters/pl-frse")
import adapter as a

facts = []
def record(item):
    facts.append(item)
    Path("/ram/facts.json").write_text(json.dumps(facts,ensure_ascii=False))

with a.execution(), a.phase("prepublication",800):
    a.prepare_collection()
    catalogue_urls=set()
    for page in range(1,3):
        url = a.listing_url("konkursy",page)
        root = a.fetch_inventory_page(lambda u,**kw:a.read_html(u,**kw),url)
        pages = a.page_numbers(root)
        assert pages <= {1,2}
        for card in a.nodes(a.one(a.nodes(root,"main"),"main"),class_name="desc_holder"):
            title = a.one(a.nodes(card,"h2"),"contest title").text()
            links = a.material_links(card,url)
            if not any(urllib.parse.urlsplit(link).hostname=="eduinspiracje.org.pl" for link in links):
                continue
            record({"type":"catalogue","page":page,"title":title,"text":card.text()[:1200],"links":links})
            catalogue_urls.update(links)
    for path in ("eduinspiracje","eduinspirator","eduinspiracje-media"):
        url=f"https://eduinspiracje.org.pl/{path}/o-konkursie"
        if url not in catalogue_urls:
            record({"type":"not_advertised","url":url})
            continue
        doc = a.read_html(url)
        content=a.detail_content(doc)
        parts = [n.text() for n in content.walk() if n.tag in ("p","li","h1","h2","h3","h4","td")]
        parts = [x[:700] for x in parts if re.search(
            r"nomin|regulamin|nagrod|202[4-8]|zgłasz|zgłos|kandyd|konkurs|termin|edycj|nabór",x,re.I)]
        links=[]
        for n in a.nodes(content,"a"):
            if "href" not in n.attrs: continue
            target=a.absolute_link(url,n.attrs["href"])
            parsed=urllib.parse.urlsplit(target)
            if parsed.scheme not in ("http","https"): continue
            links.append({"text":n.text()[:140],"url":target})
        record({"type":"overview","url":url,"evidence":parts[:30],"links":links})
print("Completed genuine public catalogue and overview research", len(facts), "items.")
