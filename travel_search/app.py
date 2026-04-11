from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

from .provider import get_provider
from .schemas import FlightSearchRequest, HotelSearchRequest


BASE_DIR = Path(__file__).resolve().parent
jinja_env = Environment(loader=FileSystemLoader(str(BASE_DIR / "templates")), cache_size=0)
templates = Jinja2Templates(directory=BASE_DIR / "templates", env=jinja_env)

app = FastAPI(title="Travel Search Tool")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request,
    origin: Optional[str] = Query(None),
    destination: Optional[str] = Query(None),
    departure_date: Optional[str] = Query(None),
    departure_time: Optional[str] = Query(None),
    return_date: Optional[str] = Query(None),
    passengers: int = Query(1, ge=1, le=9),
    cabin_class: str = Query("Economy"),
    preferred_airline: Optional[str] = Query(None),
    connection_preference: str = Query("any"),
) -> HTMLResponse:
    results: List[Dict[str, Any]] = []
    error: Optional[str] = None
    if origin and destination and departure_date:
        try:
            request_data = FlightSearchRequest(
                origin=origin,
                destination=destination,
                departure_date=departure_date,
                departure_time=departure_time,
                return_date=return_date,
                passengers=passengers,
                cabin_class=cabin_class,
                preferred_airline=preferred_airline,
                connection_preference=connection_preference,
            )
            provider = get_provider()
            results = [item.model_dump() for item in provider.search_flights(request_data)]
        except Exception as exc:
            error = str(exc)

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "results": results,
            "error": error,
            "query": {
                "origin": origin or "",
                "destination": destination or "",
                "departure_date": departure_date or "",
                "departure_time": departure_time or "",
                "return_date": return_date or "",
                "passengers": passengers,
                "cabin_class": cabin_class,
                "preferred_airline": preferred_airline or "",
                "connection_preference": connection_preference,
            },
        },
    )


@app.get("/flights", response_class=HTMLResponse)
def flight_search_page(
    request: Request,
    origin: Optional[str] = Query(None),
    destination: Optional[str] = Query(None),
    departure_date: Optional[str] = Query(None),
    departure_time: Optional[str] = Query(None),
    return_date: Optional[str] = Query(None),
    passengers: int = Query(1, ge=1, le=9),
    cabin_class: str = Query("Economy"),
    preferred_airline: Optional[str] = Query(None),
    connection_preference: str = Query("any"),
) -> HTMLResponse:
    results: List[Dict[str, Any]] = []
    error: Optional[str] = None
    if origin and destination and departure_date:
        try:
            request_data = FlightSearchRequest(
                origin=origin,
                destination=destination,
                departure_date=departure_date,
                departure_time=departure_time,
                return_date=return_date,
                passengers=passengers,
                cabin_class=cabin_class,
                preferred_airline=preferred_airline,
                connection_preference=connection_preference,
            )
            provider = get_provider()
            results = [item.model_dump() for item in provider.search_flights(request_data)]
        except Exception as exc:
            error = str(exc)

    return templates.TemplateResponse(
        "flight_search.html",
        {
            "request": request,
            "results": results,
            "error": error,
            "query": {
                "origin": origin or "",
                "destination": destination or "",
                "departure_date": departure_date or "",
                "departure_time": departure_time or "",
                "return_date": return_date or "",
                "passengers": passengers,
                "cabin_class": cabin_class,
                "preferred_airline": preferred_airline or "",
                "connection_preference": connection_preference,
            },
        },
    )


@app.get("/hotels", response_class=HTMLResponse)
def hotel_search_page(
    request: Request,
    destination: Optional[str] = Query(None),
    checkin_date: Optional[str] = Query(None),
    checkout_date: Optional[str] = Query(None),
    guests: int = Query(1, ge=1, le=10),
    rooms: int = Query(1, ge=1, le=5),
) -> HTMLResponse:
    results: List[Dict[str, Any]] = []
    error: Optional[str] = None

    if destination and checkin_date and checkout_date:
        try:
            request_data = HotelSearchRequest(
                destination=destination,
                checkin_date=checkin_date,
                checkout_date=checkout_date,
                guests=guests,
                rooms=rooms,
            )
            provider = get_provider()
            results = [item.model_dump() for item in provider.search_hotels(request_data)]
        except Exception as exc:
            error = str(exc)

    return templates.TemplateResponse(
        "hotel_search.html",
        {
            "request": request,
            "results": results,
            "error": error,
            "query": {
                "destination": destination or "",
                "checkin_date": checkin_date or "",
                "checkout_date": checkout_date or "",
                "guests": guests,
                "rooms": rooms,
            },
        },
    )


@app.get("/api/flights", response_class=JSONResponse)
def api_flights(
    origin: str = Query(...),
    destination: str = Query(...),
    departure_date: str = Query(...),
    departure_time: Optional[str] = Query(None),
    return_date: Optional[str] = Query(None),
    passengers: int = Query(1, ge=1, le=9),
    cabin_class: str = Query("Economy"),
    preferred_airline: Optional[str] = Query(None),
    connection_preference: str = Query("any"),
) -> Dict[str, Any]:
    request_data = FlightSearchRequest(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        departure_time=departure_time,
        return_date=return_date,
        passengers=passengers,
        cabin_class=cabin_class,
        preferred_airline=preferred_airline,
        connection_preference=connection_preference,
    )
    provider = get_provider()
    return {"results": [item.model_dump() for item in provider.search_flights(request_data)]}


@app.get("/api/hotels", response_class=JSONResponse)
def api_hotels(
    destination: str = Query(...),
    checkin_date: str = Query(...),
    checkout_date: str = Query(...),
    guests: int = Query(1, ge=1, le=10),
    rooms: int = Query(1, ge=1, le=5),
) -> Dict[str, Any]:
    request_data = HotelSearchRequest(
        destination=destination,
        checkin_date=checkin_date,
        checkout_date=checkout_date,
        guests=guests,
        rooms=rooms,
    )
    provider = get_provider()
    return {"results": [item.model_dump() for item in provider.search_hotels(request_data)]}


@app.get("/docs")
def redirect_docs() -> RedirectResponse:
    return RedirectResponse(url="/redoc")
