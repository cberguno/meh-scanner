import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from .provider import get_provider
from .schemas import FlightSearchRequest, HotelSearchRequest


def _format_flight_row(result: Any) -> str:
    return (
        f"{result.airline} | {result.origin}->{result.destination} | "
        f"{result.departure_date} {result.departure_time or 'any'} | "
        f"{result.connection_type or 'any'} | {result.cabin_class} | "
        f"{result.price} {result.currency} | {result.booking_url}"
    )


def _format_hotel_row(result: Any) -> str:
    return (
        f"{result.name} | {result.destination} | {result.rating}★ | {result.room_type} | "
        f"{result.price_per_night} {result.currency}/night | {result.total_price} total | {result.booking_url}"
    )


def _print_results(results: List[Any], is_json: bool) -> None:
    if is_json:
        print(json.dumps([result.model_dump() for result in results], indent=2, default=str))
        return

    for result in results:
        if hasattr(result, "airline"):
            print(_format_flight_row(result))
        else:
            print(_format_hotel_row(result))
    print(f"\n{len(results)} result(s) returned.")


def _configure_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Travel search CLI")
    parser.add_argument("--provider", help="Travel provider to use (default: mock)")
    parser.add_argument("--json", action="store_true", help="Print results as JSON")

    subparsers = parser.add_subparsers(dest="command", required=True)

    flight_parser = subparsers.add_parser("flight", help="Search for flights")
    flight_parser.add_argument("--origin", required=True, help="Origin airport or city code")
    flight_parser.add_argument("--destination", required=True, help="Destination airport or city code")
    flight_parser.add_argument("--departure-date", required=True, help="Departure date (YYYY-MM-DD)")
    flight_parser.add_argument("--departure-time", help="Preferred departure time (HH:MM)")
    flight_parser.add_argument("--return-date", help="Return date (YYYY-MM-DD)")
    flight_parser.add_argument("--passengers", type=int, default=1, help="Number of passengers")
    flight_parser.add_argument("--cabin-class", default="Economy", help="Cabin class")
    flight_parser.add_argument("--preferred-airline", help="Preferred airline name")
    flight_parser.add_argument(
        "--connection-preference",
        choices=["any", "nonstop", "1 stop"],
        default="any",
        help="Preferred connection type",
    )

    hotel_parser = subparsers.add_parser("hotel", help="Search for hotels")
    hotel_parser.add_argument("--destination", required=True, help="Hotel destination city")
    hotel_parser.add_argument("--checkin-date", required=True, help="Check-in date (YYYY-MM-DD)")
    hotel_parser.add_argument("--checkout-date", required=True, help="Checkout date (YYYY-MM-DD)")
    hotel_parser.add_argument("--guests", type=int, default=1, help="Number of guests")
    hotel_parser.add_argument("--rooms", type=int, default=1, help="Number of rooms")

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    parser = _configure_parser()
    args = parser.parse_args(argv)
    provider = get_provider(args.provider)

    if args.command == "flight":
        request = FlightSearchRequest(
            origin=args.origin,
            destination=args.destination,
            departure_date=args.departure_date,
            departure_time=args.departure_time,
            return_date=args.return_date,
            passengers=args.passengers,
            cabin_class=args.cabin_class,
            preferred_airline=args.preferred_airline,
            connection_preference=args.connection_preference,
            provider=args.provider,
        )
        results = provider.search_flights(request)
    else:
        request = HotelSearchRequest(
            destination=args.destination,
            checkin_date=args.checkin_date,
            checkout_date=args.checkout_date,
            guests=args.guests,
            rooms=args.rooms,
            provider=args.provider,
        )
        results = provider.search_hotels(request)

    _print_results(results, args.json)


if __name__ == "__main__":
    main()
