import hashlib
from abc import ABC, abstractmethod
from typing import List, Optional

from .config import TravelConfig
from .schemas import (
    FlightResult,
    FlightSearchRequest,
    HotelResult,
    HotelSearchRequest,
)


class TravelProvider(ABC):
    @abstractmethod
    def search_flights(self, request: FlightSearchRequest) -> List[FlightResult]:
        raise NotImplementedError

    @abstractmethod
    def search_hotels(self, request: HotelSearchRequest) -> List[HotelResult]:
        raise NotImplementedError


def _stable_seed(*values: Optional[str]) -> int:
    normalized = "|".join([str(value or "").strip().lower() for value in values])
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def get_provider(name: Optional[str] = None) -> TravelProvider:
    provider_name = (name or TravelConfig.TRAVEL_PROVIDER or "mock").strip().lower()
    if provider_name == "mock":
        return MockTravelProvider()
    raise ValueError(f"Unsupported travel provider: {provider_name}")


class MockTravelProvider(TravelProvider):
    def search_flights(self, request: FlightSearchRequest) -> List[FlightResult]:
        carriers = ["MockAir", "SkyQuest", "TravelBee"]
        if request.preferred_airline:
            matches = [c for c in carriers if request.preferred_airline.lower() in c.lower()]
            others = [c for c in carriers if c not in matches]
            carriers = matches + others

        results: List[FlightResult] = []
        base_seed = _stable_seed(
            request.origin,
            request.destination,
            request.departure_date.isoformat(),
            request.departure_time,
            request.preferred_airline,
            request.connection_preference,
        )
        for index, carrier in enumerate(carriers[: TravelConfig.MAX_FLIGHT_RESULTS]):
            price_value = 120 + (base_seed % 180) + index * 40 + request.passengers * 12
            if request.return_date:
                price_value += 80
            if request.preferred_airline and request.preferred_airline.lower() in carrier.lower():
                price_value -= 15

            connection_type = request.connection_preference
            if connection_type == "any":
                connection_type = "nonstop" if index == 0 else "1 stop"

            departure_time = request.departure_time or f"0{8 + index}:00" if index < 2 else "12:00"
            itinerary = f"{request.origin} → {request.destination}"
            if request.return_date:
                itinerary += f" → {request.origin}"

            results.append(
                FlightResult(
                    provider="Mock Travel",
                    origin=request.origin,
                    destination=request.destination,
                    departure_date=request.departure_date,
                    return_date=request.return_date,
                    departure_time=departure_time,
                    airline=carrier,
                    connection_type=connection_type,
                    itinerary=itinerary,
                    cabin_class=request.cabin_class,
                    price=f"{price_value}.99",
                    currency=TravelConfig.TRAVEL_CURRENCY,
                    booking_url=f"https://book.mocktravel.example.com/flight/{carrier.lower()}?from={request.origin}&to={request.destination}&time={departure_time}",
                )
            )
        return results

    def search_hotels(self, request: HotelSearchRequest) -> List[HotelResult]:
        hotel_names = ["Park View Suites", "City Central Inn", "StayEasy Hotel"]
        room_types = ["Standard Room", "Deluxe Room", "Suite"]
        results: List[HotelResult] = []
        nights = (request.checkout_date - request.checkin_date).days
        base_seed = _stable_seed(request.destination, request.checkin_date.isoformat())
        for index, hotel_name in enumerate(hotel_names[: TravelConfig.MAX_HOTEL_RESULTS]):
            rating = 3 + (index % 3)
            nightly_price = 90 + (base_seed % 120) + index * 25 + request.guests * 8
            total_price = nightly_price * nights * request.rooms
            results.append(
                HotelResult(
                    provider="Mock Travel",
                    destination=request.destination,
                    name=f"{hotel_name} {request.destination}",
                    rating=rating,
                    room_type=room_types[index % len(room_types)],
                    price_per_night=f"{nightly_price}.00",
                    total_price=f"{total_price}.00",
                    currency=TravelConfig.TRAVEL_CURRENCY,
                    checkin_date=request.checkin_date,
                    checkout_date=request.checkout_date,
                    guests=request.guests,
                    rooms=request.rooms,
                    booking_url=f"https://book.mocktravel.example.com/hotel/{hotel_name.replace(' ', '-').lower()}?city={request.destination}",
                )
            )
        return results
