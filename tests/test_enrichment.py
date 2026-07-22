import json
import pytest
from unittest.mock import MagicMock

from core import enrichment


@pytest.fixture(autouse=True)
def clear_enrichment_cache():
    """Resets the in-memory cache between tests."""
    enrichment._enrichment_cache.clear()


def test_enrich_hop_via_cymru_success(monkeypatch):
    """Validates a DNS-based Cymru resolution for public IP addresses."""
    monkeypatch.setattr(enrichment, "DNSPYTHON_AVAILABLE", True)

    mock_origin_answer = MagicMock()
    mock_origin_answer.to_text.return_value = '"3356 | 8.8.8.0/24 | US | ripencc | 1999-01-01"'
    
    mock_desc_answer = MagicMock()
    mock_desc_answer.to_text.return_value = '"3356 | US | arin | 1998-01-01 | LEVEL3, US"'
    
    mock_resolver = MagicMock()
    mock_resolver.resolve.side_effect = [[mock_origin_answer], [mock_desc_answer]]
    monkeypatch.setattr(enrichment, "GLOBAL_RESOLVER", mock_resolver)

    result = enrichment.enrich_hop("8.8.8.8")

    assert result["status"] == "confirmed"
    assert result["org"] == "LEVEL3, US"
    assert result["asn"] == "3356"
    assert result["country"] == "US"
    assert result["source"] == "cymru"


def test_enrich_hop_fallback_to_rdap(monkeypatch):
    """Validates a fallback to RDAP when dnspython is unavailable."""
    monkeypatch.setattr(enrichment, "DNSPYTHON_AVAILABLE", False)

    mock_response = MagicMock()
    rdap_payload = {
        "entities": [
            {"vcardArray": ["vcard", [["version", {}, "text", "4.0"], ["fn", {}, "text", "Google LLC"]]]}
        ],
        "country": "US",
        "name": "GOOGLE"
    }
    mock_response.read.return_value = bytes(json.dumps(rdap_payload), "utf-8")
    mock_response.__enter__.return_value = mock_response
    
    mock_urlopen = MagicMock(return_value=mock_response)
    monkeypatch.setattr(enrichment.urllib.request, "urlopen", mock_urlopen)

    result = enrichment.enrich_hop("8.8.8.8")

    assert result["status"] == "confirmed"
    assert result["org"] == "Google LLC"
    assert result["asn"] is None
    assert result["country"] == "US"
    assert result["source"] == "rdap"


@pytest.mark.parametrize("private_ip", ["192.168.1.1", "10.0.0.5", "127.0.0.1", "???"])
def test_enrich_hop_private_or_invalid_ips_skipped(private_ip):
    """Validates that private and invalid IPs skip network calls."""
    result = enrichment.enrich_hop(private_ip)
    
    assert result["status"] == "unavailable"
    assert result["org"] is None
    assert result["asn"] is None
    assert result["source"] is None


def test_enrich_hops_list_processing(monkeypatch):
    """Ensures enrich_hops processes hop lists safely, without mutating the input."""
    mock_enrich = MagicMock(return_value={"status": "unavailable", "org": None, "asn": None, "country": None, "source": None})
    monkeypatch.setattr(enrichment, "enrich_hop", mock_enrich)
    
    input_hops = [
        {"hop": 1, "host": "192.168.1.1", "loss": 0.0, "delay": 1.2},
        {"hop": 2, "host": "8.8.8.8", "loss": 0.0, "delay": 14.5}
    ]

    output_hops = enrichment.enrich_hops(input_hops)

    assert input_hops[0] is not output_hops[0]
    assert "enrichment" in output_hops[0]
    assert "enrichment" not in input_hops[0]
    assert len(output_hops) == 2