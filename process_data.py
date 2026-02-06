import json
import mwparserfromhell
import gzip
import bz2
import re
import os
import argparse
import xml.etree.ElementTree as ET
from urllib.request import urlopen, urlretrieve
from html.parser import HTMLParser

OUT_JSON = "smoldata.json"
WIKI_DUMPS_URL = "https://dumps.wikimedia.org/simplewiki/"

all_categories = {}
all_pages = {}


class DumpLinkParser(HTMLParser):
    """Extract date-stamped directory links from the Wikimedia dumps index page."""
    def __init__(self):
        super().__init__()
        self.dates = []

    def handle_data(self, data):
        data = data.strip().rstrip("/")
        if re.match(r"^\d{8}$", data):
            self.dates.append(data)


def find_latest_dump():
    """Find the most recent completed dump date from Wikimedia."""
    print("Checking for latest dump date...")
    with urlopen(WIKI_DUMPS_URL) as resp:
        html = resp.read().decode()
    parser = DumpLinkParser()
    parser.feed(html)
    parser.dates.sort(reverse=True)
    for date in parser.dates:
        status_url = f"{WIKI_DUMPS_URL}{date}/dumpstatus.json"
        try:
            with urlopen(status_url) as resp:
                status = json.loads(resp.read())
            if status.get("jobs", {}).get("articlesmultistreamdump", {}).get("status") == "done":
                print(f"Latest complete dump: {date}")
                return date
        except Exception:
            continue
    raise RuntimeError("No completed dump found at " + WIKI_DUMPS_URL)


def download_dumps(date, dumps_dir):
    """Download article and pagelinks dumps if not already present."""
    articles = f"simplewiki-{date}-pages-articles-multistream.xml.bz2"
    pagelinks = f"simplewiki-{date}-pagelinks.sql.gz"

    articles_path = os.path.join(dumps_dir, articles)
    pagelinks_path = os.path.join(dumps_dir, pagelinks)

    for filename, path in [(articles, articles_path), (pagelinks, pagelinks_path)]:
        if os.path.exists(path):
            print(f"Already exists: {path}")
            continue
        url = f"{WIKI_DUMPS_URL}{date}/{filename}"
        print(f"Downloading {url} ...")
        urlretrieve(url, path)
        print(f"Downloaded: {path}")

    return articles_path, pagelinks_path


def process_page(title, page_id, raw_text):
    """Extract summary text, categories, thumbnail, and disambiguation flag from a wiki page."""
    if raw_text.upper().startswith("#REDIRECT"):
        return

    # Strip markup lines (tables, infoboxes, lists, etc.), keep prose
    text_toparse = "\n".join(
        x for x in raw_text.split("\n")
        if not x.startswith("thumb|")
        if not x.upper().startswith("__NOTOC__")
        and (len(x) == 0 or x[0] not in "{}[]|&<>-*= ")
    ).strip("\n")
    # First paragraph, max 8 lines
    text_toparse = "\n".join(text_toparse.split("\n\n")[0].split("\n")[:8])

    parsed_text = mwparserfromhell.parse(text_toparse).strip_code().strip()

    # Truncate to ~300 chars at sentence boundaries
    while len(parsed_text) > 300 and len(dot_split := parsed_text.split(".")) > 2:
        parsed_text = ".".join(dot_split[:-2]) + "."
    while len(parsed_text) > 300 and len(dot_split := parsed_text.split("\n")) > 2:
        parsed_text = "\n".join(dot_split[:-1])

    # Extract categories from [[Category:...]] markup
    categories = [
        cat.split("|")[0].split("]")[0].strip()
           .replace("\u200E", "").replace("\u200F", "").replace("_", " ")
        for cat in raw_text.lower().split("[[category:")[1:]
    ]
    if "{{songs category" in raw_text.lower():
        categories.append(title.lower().replace("category:", "")[:-len(" songs")])

    # Extract thumbnail from infobox fields or [[File:]] reference
    thumb = None
    for field in ["logo", "screenshot", "cover", "image", "map"]:
        result = re.search(rf'\| *{field} *=(.+)', raw_text, re.IGNORECASE)
        if result:
            thumb = result.group(1).strip()
            break
    if thumb is None and "[[File:" in raw_text:
        thumb = raw_text.split("[[File:")[1].split("|")[0].split("]")[0].strip()
    if thumb is not None and len(thumb.strip()) == 0:
        thumb = f"{title}.png"

    is_disambiguation = any(
        t in raw_text.lower()
        for t in ["{{disambiguation}}", "{{disambig}}", "{{numberdis}}"]
    )

    all_pages[title] = {
        "id": page_id,
        "title": title,
        "text": parsed_text,
        "categories": categories,
        "thumb": thumb,
        "disambiguation": is_disambiguation,
    }

    for category in categories:
        if category not in all_categories:
            all_categories[category] = []
        all_categories[category].append(title)


def parse_pagelinks(pagelinks_path):
    """Parse pagelinks SQL dump into a dict of page_id -> [linked_page_ids]."""
    print("Processing pagelinks...")
    links = {}
    INSERT_SYNTAX = "INSERT INTO `pagelinks` VALUES "
    with gzip.open(pagelinks_path, "rt") as f:
        for line in f:
            if not line.startswith(INSERT_SYNTAX):
                continue
            for v in line[len(INSERT_SYNTAX) + 1:-3].split("),("):
                a, _, b = v.split(",")
                a, b = int(a), int(b)
                if a not in links:
                    links[a] = []
                links[a].append(b)
    return links


def parse_articles(articles_path):
    """Stream-parse the MediaWiki XML dump using iterparse."""
    print("Processing articles...")
    page_count = 0

    with bz2.open(articles_path, "rb") as f:
        # Detect XML namespace from the root element
        ns = ""
        for event, elem in ET.iterparse(f, events=("start",)):
            if elem.tag.startswith("{"):
                ns = elem.tag.split("}")[0] + "}"
            break

        # Re-open and parse page elements
    with bz2.open(articles_path, "rb") as f:
        for event, elem in ET.iterparse(f, events=("end",)):
            if elem.tag != f"{ns}page":
                continue

            page_count += 1
            if page_count % 10000 == 0:
                print(f"  {page_count} pages processed")

            title_elem = elem.find(f"{ns}title")
            id_elem = elem.find(f"{ns}id")
            text_elem = elem.find(f".//{ns}text")

            if title_elem is None or id_elem is None or text_elem is None or not text_elem.text:
                elem.clear()
                continue

            process_page(title_elem.text, int(id_elem.text), text_elem.text)
            elem.clear()

    print(f"  {page_count} pages total")


def build_subcategories():
    """Map each subcategory to its parent categories."""
    print("Building subcategory hierarchy...")
    sub_categories = {}
    for cat_name, members in all_categories.items():
        for member in members:
            if not member.lower().startswith("category:"):
                continue
            sub_val = member.lower().split("category:")[1]
            if sub_val not in sub_categories:
                sub_categories[sub_val] = []
            sub_categories[sub_val].append(cat_name)
    return sub_categories


def expand_categories(page_categories, sub_categories, cache):
    """Recursively expand categories through the subcategory tree."""
    result = set()
    for cat in page_categories:
        if cat in cache:
            result.update(cache[cat])
            continue
        # Cycle guard: mark as being computed
        cache[cat] = frozenset()
        parents = sub_categories.get(cat, [])
        expanded = expand_categories(parents, sub_categories, cache)
        cache[cat] = expanded | {cat}
        result.update(cache[cat])
    return result


def main():
    parser = argparse.ArgumentParser(description="Process Wikipedia dumps for Xikipedia")
    parser.add_argument("--dumps-dir", default=".", help="Directory containing or to download dump files into")
    parser.add_argument("--output-dir", default=".", help="Directory for output files")
    parser.add_argument("--date", default=None, help="Dump date (YYYYMMDD). Auto-detects latest if omitted.")
    parser.add_argument("--skip-download", action="store_true", help="Skip auto-download, use existing files")
    args = parser.parse_args()

    os.makedirs(args.dumps_dir, exist_ok=True)
    os.makedirs(args.output_dir, exist_ok=True)

    if args.skip_download:
        # Find existing dump files in the directory
        bz2_files = [f for f in os.listdir(args.dumps_dir) if f.endswith("-pages-articles-multistream.xml.bz2")]
        gz_files = [f for f in os.listdir(args.dumps_dir) if f.endswith("-pagelinks.sql.gz")]
        if not bz2_files or not gz_files:
            raise FileNotFoundError(f"No dump files found in {args.dumps_dir}")
        articles_path = os.path.join(args.dumps_dir, sorted(bz2_files)[-1])
        pagelinks_path = os.path.join(args.dumps_dir, sorted(gz_files)[-1])
        print(f"Using: {articles_path}")
        print(f"Using: {pagelinks_path}")
    else:
        date = args.date or find_latest_dump()
        articles_path, pagelinks_path = download_dumps(date, args.dumps_dir)

    # Phase 1: Parse pagelinks
    links = parse_pagelinks(pagelinks_path)

    # Phase 2: Parse articles
    parse_articles(articles_path)

    # Phase 3: Build subcategory hierarchy
    sub_categories = build_subcategories()

    # Phase 4: Precompute expanded categories and build final output
    print("Expanding categories and building output...")
    expand_cache = {}
    pages_out = []
    filtered_titles = []

    # Build id-to-title map for resolving pagelinks
    id_to_title = {}
    for page in all_pages.values():
        id_to_title[page["id"]] = page["title"]

    for page in all_pages.values():
        title = page["title"]
        page_id = page["id"]

        # Filter out disambiguation, year-only, empty, and namespace pages
        is_filtered = (
            page["disambiguation"]
            or len(re.sub(r"[\s0-9]{2,4}", "", page["text"])) == 0
            or re.match(r"^[0-9]{2,4}s?$", title)
            or (":" in title and title.lower().split(":")[0] in
                ["module", "category", "template", "wikimedia", "mediawiki", "wikipedia", "help"])
        )

        if is_filtered:
            filtered_titles.append(title)
            # Still register in id_to_title (already done above)
            continue

        # Expand categories through subcategory tree
        all_cats = expand_categories(page["categories"], sub_categories, expand_cache)

        # Add pagelink categories as p:{title}
        all_cats.add(f"p:{title}")
        for link_id in links.get(page_id, []):
            link_title = id_to_title.get(link_id)
            if link_title:
                all_cats.add(f"p:{link_title}")

        pages_out.append([title, page_id, page["text"], page["thumb"], sorted(all_cats)])

    print(f"  {len(pages_out)} pages in feed, {len(filtered_titles)} filtered out")

    # Phase 5: Write output
    out_json = os.path.join(args.output_dir, OUT_JSON)
    out_gz = out_json + ".gz"

    output = {"pages": pages_out, "filteredTitles": filtered_titles}

    print(f"Writing {out_json} ...")
    with open(out_json, "w") as f:
        json.dump(output, f, separators=(",", ":"))

    json_size = os.path.getsize(out_json)

    print(f"Writing {out_gz} ...")
    with gzip.open(out_gz, "wt") as f:
        json.dump(output, f, separators=(",", ":"))

    gz_size = os.path.getsize(out_gz)

    # Phase 6: Generate version.json
    version = {"html": "1.2.0", "sw": "1.2.0", "simple": json_size}
    version_path = os.path.join(args.output_dir, "version.json")
    print(f"Writing {version_path} ...")
    with open(version_path, "w") as f:
        json.dump(version, f, indent=2)

    print(f"Done. JSON: {json_size / 1e6:.1f}MB, gzip: {gz_size / 1e6:.1f}MB")


if __name__ == "__main__":
    main()
