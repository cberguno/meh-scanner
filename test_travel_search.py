from fastapi.testclient import TestClient

from travel_search.app import app
from travel_search.provider import get_provider
from travel_search.schemas import FlightSearchRequest, HotelSearchRequest


def test_mock_flight_provider_results():
    provider = get_provider("mock")
    request = FlightSearchRequest(
        origin="SFO",
        destination="LAX",
        departure_date="2026-05-01",
        return_date="2026-05-07",
        passengers=1,
        cabin_class="Economy",
    )
    results = provider.search_flights(request)

    assert len(results) >= 3
    assert all(result.origin == "SFO" for result in results)
    assert all(result.destination == "LAX" for result in results)
    assert all(result.price.endswith(".99") for result in results)


def test_mock_flight_provider_preferences():
    provider = get_provider("mock")
    request = FlightSearchRequest(
        origin="SFO",
        destination="LAX",
        departure_date="2026-05-01",
        departure_time="09:30",
        preferred_airline="MockAir",
        connection_preference="nonstop",
        passengers=1,
        cabin_class="Economy",
    )
    results = provider.search_flights(request)

    assert results[0].airline == "MockAir"
    assert results[0].departure_time == "09:30"
    assert results[0].connection_type == "nonstop"
    assert all(result.origin == "SFO" for result in results)
    assert all(result.destination == "LAX" for result in results)


def test_mock_hotel_provider_results():
    provider = get_provider("mock")
    request = HotelSearchRequest(
        destination="Paris",
        checkin_date="2026-05-01",
        checkout_date="2026-05-05",
        guests=2,
        rooms=1,
    )
    results = provider.search_hotels(request)

    assert len(results) >= 3
    assert all(result.destination == "Paris" for result in results)
    assert all(result.price_per_night.endswith(".00") for result in results)
    assert all(result.total_price.endswith(".00") for result in results)


def test_api_flights_endpoint():
    client = TestClient(app)
    response = client.get(
        "/api/flights",
        params={
            "origin": "SFO",
            "destination": "LAX",
            "departure_date": "2026-05-01",
            "return_date": "2026-05-07",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "results" in payload
    assert isinstance(payload["results"], list)
    assert payload["results"]


def test_dashboard_route_renders_and_searches():
    client = TestClient(app)
    response = client.get(
        "/",
        params={
            "origin": "SFO",
            "destination": "LAX",
            "departure_date": "2026-05-01",
            "departure_time": "09:30",
            "preferred_airline": "MockAir",
            "connection_preference": "nonstop",
        },
    )

    assert response.status_code == 200
    assert "Travel Search Dashboard" in response.text
    assert "MockAir" in response.text
    assert "09:30" in response.text


def test_api_hotels_endpoint():
    client = TestClient(app)
    response = client.get(
        "/api/hotels",
        params={
            "destination": "Paris",
            "checkin_date": "2026-05-01",
            "checkout_date": "2026-05-05",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "results" in payload
    assert isinstance(payload["results"], list)
    assert payload["results"]
