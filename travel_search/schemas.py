from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, constr, field_validator, model_validator


class FlightSearchRequest(BaseModel):
    origin: str = Field(..., min_length=2, max_length=10)
    destination: str = Field(..., min_length=2, max_length=10)
    departure_date: date
    return_date: Optional[date] = None
    departure_time: Optional[constr(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")] = None
    passengers: int = Field(1, ge=1, le=9)
    cabin_class: str = Field("Economy")
    preferred_airline: Optional[str] = None
    connection_preference: Literal["any", "nonstop", "1 stop"] = "any"
    provider: Optional[str] = None

    @field_validator("origin", "destination")
    def normalize_airport(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def validate_destination(self):
        if self.origin and self.destination and self.origin == self.destination:
            raise ValueError("Origin and destination must differ.")
        return self


class HotelSearchRequest(BaseModel):
    destination: str = Field(..., min_length=2, max_length=100)
    checkin_date: date
    checkout_date: date
    guests: int = Field(1, ge=1, le=10)
    rooms: int = Field(1, ge=1, le=5)
    provider: Optional[str] = None

    @field_validator("destination")
    def normalize_destination(cls, value: str) -> str:
        return value.strip().title()

    @model_validator(mode="after")
    def validate_dates(self):
        if self.checkin_date and self.checkout_date and self.checkout_date <= self.checkin_date:
            raise ValueError("Checkout date must be after check-in date.")
        return self


class FlightResult(BaseModel):
    provider: str
    origin: str
    destination: str
    departure_date: date
    return_date: Optional[date]
    departure_time: Optional[str] = None
    airline: str
    connection_type: str
    itinerary: str
    cabin_class: str
    price: str
    currency: str
    booking_url: str


class HotelResult(BaseModel):
    provider: str
    destination: str
    name: str
    rating: int
    room_type: str
    price_per_night: str
    total_price: str
    currency: str
    checkin_date: date
    checkout_date: date
    guests: int
    rooms: int
    booking_url: str


class SearchResponse(BaseModel):
    results: List[BaseModel]
