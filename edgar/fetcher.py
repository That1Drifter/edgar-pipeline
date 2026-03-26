"""
EDGAR Document Fetcher

SEC requires User-Agent in format: 'CompanyName Email' (e.g. 'MyApp admin@example.com')
Rate limit: 10 requests/second. We stay well under that.
All SEC responses are gzip-encoded.
"""

import urllib.request
import urllib.parse
import json
import gzip
import time
import re

USER_AGENT = "EdgarPipeline admin@example.com"
_last_request = 0
_ticker_cache = None


def _get(url: str) -> bytes:
    """Make a rate-limited request to SEC. Returns raw bytes (decompressed)."""
    global _last_request
    elapsed = time.time() - _last_request
    if elapsed < 0.15:
        time.sleep(0.15 - elapsed)

    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        _last_request = time.time()
        raw = resp.read()
        if raw[:2] == b'\x1f\x8b':
            raw = gzip.decompress(raw)
        return raw


def _get_json(url: str) -> dict:
    """Fetch a URL and parse as JSON."""
    return json.loads(_get(url))


def _get_text(url: str) -> str:
    """Fetch a URL and decode as text."""
    return _get(url).decode("utf-8", errors="replace")


def _load_tickers() -> dict:
    """Load the SEC company tickers index. Cached after first call."""
    global _ticker_cache
    if _ticker_cache is None:
        data = _get_json("https://www.sec.gov/files/company_tickers.json")
        _ticker_cache = {}
        for entry in data.values():
            name = entry.get("title", "").lower()
            _ticker_cache[name] = {
                "cik": str(entry["cik_str"]).zfill(10),
                "ticker": entry.get("ticker", ""),
                "name": entry.get("title", ""),
            }
    return _ticker_cache


def lookup_cik(company: str) -> str | None:
    """Look up a company's CIK number by name. Fuzzy match."""
    tickers = _load_tickers()
    query = company.lower().strip()

    # Exact match
    if query in tickers:
        return tickers[query]["cik"]

    # Partial match
    for name, info in tickers.items():
        if query in name or name in query:
            return info["cik"]

    # Ticker match
    for name, info in tickers.items():
        if info["ticker"].lower() == query:
            return info["cik"]

    return None


def get_company_info(cik: str) -> dict:
    """Get basic company info from SEC submissions API."""
    url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
    data = _get_json(url)
    return {
        "name": data.get("name", ""),
        "cik": data.get("cik", cik),
        "tickers": data.get("tickers", []),
        "sic": data.get("sic", ""),
        "sic_description": data.get("sicDescription", ""),
    }


def get_company_filings(cik: str, form_type: str = "10-K") -> list[dict]:
    """Get filings for a specific CIK number."""
    url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
    data = _get_json(url)

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    filings = []
    for i, form in enumerate(forms):
        if form == form_type:
            acc_clean = accessions[i].replace("-", "")
            cik_clean = cik.zfill(10).lstrip("0") or "0"
            filings.append({
                "form_type": form,
                "filed": dates[i],
                "accession": accessions[i],
                "url": f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{acc_clean}/{primary_docs[i]}",
            })
            if len(filings) >= 5:
                break
    return filings


def fetch_filing_text(url: str, max_chars: int = 80000) -> str:
    """Fetch the text content of a filing, strip HTML/XBRL, truncate."""
    try:
        text = _get_text(url)
        # Strip XBRL processing instructions and inline tags
        clean = re.sub(r'<ix:[^>]*?>', '', text)
        clean = re.sub(r'</ix:[^>]*?>', '', clean)
        clean = re.sub(r'<xbrli?:[^>]*?/>', '', clean)
        clean = re.sub(r'</?xbrli?:[^>]*?>', '', clean)
        # Strip style/script blocks
        clean = re.sub(r'<style[^>]*>.*?</style>', ' ', clean, flags=re.DOTALL)
        clean = re.sub(r'<script[^>]*>.*?</script>', ' ', clean, flags=re.DOTALL)
        # Strip hidden elements
        clean = re.sub(r'<[^>]*display\s*:\s*none[^>]*>.*?</[^>]+>', ' ', clean, flags=re.DOTALL)
        # Convert table cells and rows to readable format
        clean = re.sub(r'</t[dh]>', ' | ', clean)
        clean = re.sub(r'</tr>', '\n', clean)
        # Strip remaining HTML tags
        clean = re.sub(r'<[^>]+>', ' ', clean)
        # Decode entities
        clean = re.sub(r'&nbsp;', ' ', clean)
        clean = re.sub(r'&amp;', '&', clean)
        clean = re.sub(r'&lt;', '<', clean)
        clean = re.sub(r'&gt;', '>', clean)
        clean = re.sub(r'&[a-z]+;', ' ', clean)
        clean = re.sub(r'&#\d+;', ' ', clean)
        # Collapse whitespace but preserve newlines for table structure
        clean = re.sub(r'[^\S\n]+', ' ', clean)
        clean = re.sub(r'\n\s*\n', '\n\n', clean)
        clean = re.sub(r'\n{3,}', '\n\n', clean)
        # Skip past XBRL context block (can be 10K+ chars of machine data)
        markers = ['UNITED STATES', 'FORM 10-K', 'FORM 10-Q', 'ANNUAL REPORT', 'QUARTERLY REPORT']
        for marker in markers:
            idx = clean.find(marker)
            if idx > 0:
                clean = clean[idx:]
                break

        # For large filings, try to include the financial statements
        # They're usually in "Item 8" and contain the actual numbers
        if len(clean) > max_chars:
            # Find the financial statements section
            fin_markers = [
                'CONSOLIDATED STATEMENTS OF OPERATIONS',
                'CONSOLIDATED BALANCE SHEET',
                'Consolidated Statements of Operations',
                'Consolidated Balance Sheet',
                'FINANCIAL STATEMENTS',
                'Item 8.',
                'Item\xa08.',
            ]
            fin_start = -1
            for fm in fin_markers:
                idx = clean.find(fm)
                if idx > 0:
                    fin_start = idx
                    break

            if fin_start > 0:
                # Return: first part (cover + TOC + risk factors) + financial section
                header_budget = max_chars // 3  # 1/3 for the front matter
                fin_budget = max_chars - header_budget  # 2/3 for financials
                header = clean[:header_budget]
                financials = clean[fin_start:fin_start + fin_budget]
                clean = header + "\n\n[...document truncated...]\n\n" + financials
            else:
                clean = clean[:max_chars]
        return clean.strip()
    except Exception as e:
        return f"ERROR fetching {url}: {e}"


# Quick test
if __name__ == "__main__":
    print("Looking up Apple Inc...")
    cik = lookup_cik("Apple Inc")
    print(f"CIK: {cik}")

    if cik:
        info = get_company_info(cik)
        print(f"Name: {info['name']}, Ticker: {info['tickers']}")

        print("\nRecent 10-K filings:")
        filings = get_company_filings(cik, "10-K")
        for f in filings[:3]:
            print(f"  {f['filed']} — {f['url']}")

        if filings:
            print(f"\nFetching first 500 chars of most recent 10-K...")
            text = fetch_filing_text(filings[0]["url"], max_chars=500)
            print(text[:500])
