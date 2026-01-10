"""
NCBI E-utilities client for fetching GEO metadata.
Implements rate limiting, retries, and robust error handling.
"""
import logging
import time
from typing import Any
from xml.etree import ElementTree as ET

import backoff
import requests
from ratelimit import limits, sleep_and_retry

from config import settings

logger = logging.getLogger(__name__)


class NCBIClient:
    """
    Client for NCBI E-utilities API.
    Handles GEO Series (GSE) search and fetching with rate limiting.
    """

    def __init__(
        self,
        email: str | None = None,
        tool: str | None = None,
        api_key: str | None = None,
        rate_limit_qps: float | None = None,
    ):
        """
        Initialize NCBI client.

        Args:
            email: Email for NCBI (required by NCBI guidelines)
            tool: Tool name for NCBI
            api_key: NCBI API key (increases rate limit to 10 req/s)
            rate_limit_qps: Custom rate limit in queries per second
        """
        self.email = email or settings.ncbi_email
        self.tool = tool or settings.ncbi_tool
        self.api_key = api_key or settings.ncbi_api_key
        self.base_url = settings.ncbi_base_url

        # Rate limit: 3 req/s without API key, 10 req/s with API key
        if rate_limit_qps:
            self.rate_limit = rate_limit_qps
        elif self.api_key:
            self.rate_limit = 10.0
        else:
            self.rate_limit = 3.0

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": f"{self.tool} ({self.email})"})

        logger.info(
            f"Initialized NCBI client: email={self.email}, "
            f"rate_limit={self.rate_limit} req/s, api_key={'yes' if self.api_key else 'no'}"
        )

    def _get_common_params(self) -> dict[str, str]:
        """Get common parameters for all NCBI requests."""
        params = {
            "email": self.email,
            "tool": self.tool,
        }
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    @sleep_and_retry
    @limits(calls=1, period=1)  # Will be dynamically adjusted in __call__
    def _rate_limited_request(self, url: str, params: dict[str, Any]) -> requests.Response:
        """
        Make a rate-limited HTTP request.
        The decorator handles the rate limiting based on self.rate_limit.
        """
        # Dynamic sleep to respect configured rate limit
        time.sleep(1.0 / self.rate_limit)
        return self.session.get(url, params=params, timeout=30)

    @backoff.on_exception(
        backoff.expo,
        (requests.exceptions.RequestException, requests.exceptions.Timeout),
        max_tries=5,
        max_time=300,
    )
    def _make_request(self, endpoint: str, params: dict[str, Any]) -> requests.Response:
        """
        Make HTTP request with retries and exponential backoff.

        Args:
            endpoint: E-utilities endpoint (e.g., 'esearch.fcgi')
            params: Query parameters

        Returns:
            Response object

        Raises:
            requests.exceptions.RequestException: On persistent failures
        """
        url = f"{self.base_url}/{endpoint}"
        full_params = {**self._get_common_params(), **params}

        logger.debug(f"Making request to {url} with params: {full_params}")
        response = self._rate_limited_request(url, full_params)
        response.raise_for_status()
        return response

    def search_gse(
        self,
        query: str,
        retmax: int = 100,
        mindate: str | None = None,
        maxdate: str | None = None,
        retstart: int = 0,
    ) -> list[str]:
        """
        Search for GSE accessions using NCBI ESearch.

        Args:
            query: Search query (e.g., "breast cancer RNA-seq")
            retmax: Maximum number of results to return
            mindate: Minimum date filter (YYYY/MM/DD)
            maxdate: Maximum date filter (YYYY/MM/DD)
            retstart: Starting index for pagination

        Returns:
            List of GSE accession IDs

        Example:
            >>> client = NCBIClient()
            >>> gse_ids = client.search_gse("breast cancer RNA-seq[Strategy]", retmax=50)
            >>> print(gse_ids[:5])
            ['GSE123456', 'GSE123457', ...]
        """
        params: dict[str, Any] = {
            "db": "gds",
            "term": f"({query}) AND gse[Entry Type]",
            "retmax": retmax,
            "retstart": retstart,
            "retmode": "json",
            "usehistory": "y",
        }

        if mindate:
            params["mindate"] = mindate
        if maxdate:
            params["maxdate"] = maxdate

        logger.info(f"Searching GEO: query='{query}', retmax={retmax}, retstart={retstart}")

        response = self._make_request("esearch.fcgi", params)
        data = response.json()

        id_list = data.get("esearchresult", {}).get("idlist", [])
        count = data.get("esearchresult", {}).get("count", 0)

        logger.info(f"Found {count} total results, returning {len(id_list)} IDs")
        return id_list

    def fetch_gse_summary(self, gse_ids: list[str]) -> dict[str, dict[str, Any]]:
        """
        Fetch summary information for multiple GSE IDs using ESummary.

        Args:
            gse_ids: List of GEO dataset IDs (NOT GSE accessions, but NCBI UIDs)

        Returns:
            Dictionary mapping UID to summary data
        """
        if not gse_ids:
            return {}

        params = {
            "db": "gds",
            "id": ",".join(gse_ids),
            "retmode": "json",
        }

        logger.info(f"Fetching summaries for {len(gse_ids)} GSE records")

        response = self._make_request("esummary.fcgi", params)
        data = response.json()

        result = data.get("result", {})
        # Remove metadata keys
        result.pop("uids", None)

        logger.info(f"Retrieved {len(result)} summaries")
        return result

    def fetch_gse_text(self, gse_accession: str) -> dict[str, Any]:
        """
        Fetch detailed text metadata for a single GSE accession using EFetch.

        Args:
            gse_accession: GSE accession (e.g., 'GSE123456')

        Returns:
            Dictionary with parsed text fields:
                - accession: GSE accession
                - title: Study title
                - summary: Study summary
                - overall_design: Overall experimental design
                - type: Study type (Expression profiling, etc.)
                - platform_ids: List of GPL IDs
                - sample_ids: List of GSM IDs
                - pubmed_ids: List of PMIDs
                - contact: Contact information
                - submission_date, last_update_date: Date strings
                - raw_xml: Full XML response

        Example:
            >>> client = NCBIClient()
            >>> data = client.fetch_gse_text("GSE123456")
            >>> print(data['title'])
        """
        # First, search to get the UID for this accession
        search_params = {
            "db": "gds",
            "term": f"{gse_accession}[Accession]",
            "retmode": "json",
        }

        search_response = self._make_request("esearch.fcgi", search_params)
        search_data = search_response.json()
        id_list = search_data.get("esearchresult", {}).get("idlist", [])

        if not id_list:
            logger.warning(f"No records found for {gse_accession}")
            return {"accession": gse_accession, "error": "Not found"}

        uid = id_list[0]

        # Fetch full record - use summary since full XML is not always available
        # Get detailed summary first
        summary = self.fetch_gse_summary([uid])

        if uid not in summary:
            logger.warning(f"No summary data for {gse_accession}")
            return {"accession": gse_accession, "error": "No summary data available"}

        summary_data = summary[uid]

        logger.info(f"Fetching metadata for {gse_accession} (UID: {uid})")

        # Build metadata from summary
        parsed = {
            "accession": gse_accession,
            "title": summary_data.get("title", ""),
            "summary": summary_data.get("summary", ""),
            "overall_design": "",  # Not in summary
            "type": summary_data.get("gdstype", ""),
            "platform_ids": [summary_data.get("gpl", "")] if summary_data.get("gpl") else [],
            "sample_ids": [],
            "pubmed_ids": [],
            "taxon": summary_data.get("taxon", ""),
            "entrez_date": summary_data.get("pdat", ""),
            "submission_date": summary_data.get("pdat", ""),
            "n_samples": summary_data.get("n_samples", ""),
            "organisms": [summary_data.get("taxon", "")] if summary_data.get("taxon") else [],
        }

        return parsed

    def _parse_gse_xml(self, root: ET.Element, gse_accession: str) -> dict[str, Any]:
        """
        Parse GEO XML response into structured dictionary.

        Args:
            root: XML root element
            gse_accession: GSE accession for reference

        Returns:
            Parsed metadata dictionary
        """
        # Find the DocSum element
        docsum = root.find(".//DocumentSummary")
        if docsum is None:
            return {"accession": gse_accession, "error": "Invalid XML structure"}

        def get_text(elem: ET.Element | None) -> str:
            """Safely extract text from element."""
            return elem.text.strip() if elem is not None and elem.text else ""

        def get_items(parent: ET.Element, tag: str) -> list[str]:
            """Extract list of items from repeated tags."""
            return [get_text(item) for item in parent.findall(f".//{tag}") if get_text(item)]

        data: dict[str, Any] = {
            "accession": get_text(docsum.find(".//Accession")) or gse_accession,
            "title": get_text(docsum.find(".//title")),
            "summary": get_text(docsum.find(".//summary")),
            "overall_design": get_text(docsum.find(".//overall_design")),
            "type": get_text(docsum.find(".//gdsType")),
            "platform_ids": get_items(docsum, "GPL"),
            "sample_ids": get_items(docsum, "GSM"),
            "pubmed_ids": get_items(docsum, "PubMedIds/int"),
            "taxon": get_text(docsum.find(".//taxon")),
            "entrez_date": get_text(docsum.find(".//PDAT")),
            "submission_date": get_text(docsum.find(".//PDAT")),
            "n_samples": get_text(docsum.find(".//n_samples")),
        }

        # Extract sample organisms
        samples = docsum.findall(".//Sample")
        organisms = list(set(get_text(s.find(".//Organism")) for s in samples))
        data["organisms"] = [org for org in organisms if org]

        # Contact/contributor
        contributors = get_items(docsum, "Contributor")
        if contributors:
            data["contributor"] = contributors[0]

        return data

    def fetch_gse_batch(self, gse_accessions: list[str]) -> dict[str, dict[str, Any]]:
        """
        Fetch metadata for multiple GSE accessions.

        Args:
            gse_accessions: List of GSE accessions

        Returns:
            Dictionary mapping accession to metadata
        """
        results = {}
        for accession in gse_accessions:
            try:
                data = self.fetch_gse_text(accession)
                results[accession] = data
            except Exception as e:
                logger.error(f"Failed to fetch {accession}: {e}")
                results[accession] = {"accession": accession, "error": str(e)}
        return results
