# Travel Search Tool

A standalone travel search package added to the repository.

## What it includes

- Mock flight and hotel provider implementation
- CLI support for flight and hotel search
- FastAPI web app with search forms and JSON endpoints
- Pydantic request/response models

## Usage

### CLI

Search flights:

```bash
python -m travel_search.cli flight --origin SFO --destination LAX --departure-date 2026-05-01 --departure-time 09:30 --preferred-airline MockAir --connection-preference nonstop --return-date 2026-05-07
```

Search hotels:

```bash
python -m travel_search.cli hotel --destination Paris --checkin-date 2026-05-01 --checkout-date 2026-05-05
```

### Web app

Start the app with Uvicorn:

```bash
uvicorn travel_search.app:app --reload
```

Open:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/flights`
- `http://127.0.0.1:8000/hotels`

### API

Flight search:

```bash
curl 'http://127.0.0.1:8000/api/flights?origin=SFO&destination=LAX&departure_date=2026-05-01&departure_time=09:30&preferred_airline=MockAir&connection_preference=nonstop'
```

Hotel search:

```bash
curl 'http://127.0.0.1:8000/api/hotels?destination=Paris&checkin_date=2026-05-01&checkout_date=2026-05-05'
```
