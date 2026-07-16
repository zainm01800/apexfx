import urllib.parse as urlparse
import httpx

def fetch_all_rows(url: str, headers: dict, page_size: int = 1000) -> list:
    """Fetch all rows from a Supabase PostgREST endpoint by paginating.
    
    Stops when a page comes back shorter than page_size.
    """
    all_rows = []
    offset = 0
    hdrs = dict(headers)
    
    # Parse existing query parameters
    parsed = urlparse.urlparse(url)
    base_params = urlparse.parse_qs(parsed.query)
    
    # Strip any existing limit or offset to control them ourselves
    base_params.pop("limit", None)
    base_params.pop("offset", None)
    
    while True:
        params = dict(base_params)
        params["limit"] = [str(page_size)]
        params["offset"] = [str(offset)]
        
        new_query = urlparse.urlencode(params, doseq=True)
        page_url = urlparse.urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment
        ))
        
        r = httpx.get(page_url, headers=hdrs, timeout=60)
        if r.status_code != 200:
            raise Exception(f"Supabase read failed with status {r.status_code}: {r.text}")
            
        data = r.json()
        if not isinstance(data, list):
            raise Exception(f"Supabase read did not return a list: {data}")
            
        all_rows.extend(data)
        if len(data) < page_size:
            break
            
        offset += page_size
        
    return all_rows
