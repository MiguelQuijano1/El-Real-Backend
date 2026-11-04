"""Prueba del servicio de geocodificación (sin llamadas reales a Nominatim)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.geocoding import geocode, _CACHE


def setup_function():
    _CACHE.clear()


@pytest.mark.asyncio
async def test_geocode_returns_coords_on_success():
    mock_response = MagicMock()
    mock_response.json.return_value = [{"lat": "-12.1234", "lon": "-77.0234"}]
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.geocoding._http_get", new_callable=AsyncMock, return_value=mock_response):
        result = await geocode("Av. Larco 345, Miraflores, Lima")

    assert result == (-12.1234, -77.0234)


@pytest.mark.asyncio
async def test_geocode_returns_none_on_empty_response():
    mock_response = MagicMock()
    mock_response.json.return_value = []
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.geocoding._http_get", new_callable=AsyncMock, return_value=mock_response):
        result = await geocode("dirección que no existe xyz")

    assert result is None


@pytest.mark.asyncio
async def test_geocode_uses_cache_on_second_call():
    mock_response = MagicMock()
    mock_response.json.return_value = [{"lat": "-12.0", "lon": "-77.0"}]
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.geocoding._http_get", new_callable=AsyncMock, return_value=mock_response) as mock_get:
        await geocode("Av. Test 123")
        await geocode("Av. Test 123")  # segunda llamada

    assert mock_get.call_count == 1  # solo llamó una vez
