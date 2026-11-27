import os
import json
import requests

# Zentrale Definition irreversibler Tools
IRREVERSIBLE_TOOLS = {
    "create_appointment",
}

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_calendar_events",
            "description": "Get calendar appointments for a specific date (YYYY-MM-DD).",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format (e.g. '2026-10-29').",
                    }
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_appointment",
            "description": "Create a new appointment in the user's calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format (e.g. '2026-10-29').",
                    },
                    "start": {
                        "type": "string",
                        "description": "Start time in HH:MM 24-hour format (e.g. '17:00').",
                    },
                    "end": {
                        "type": "string",
                        "description": "End time in HH:MM 24-hour format (e.g. '18:00').",
                    },
                    "title": {
                        "type": "string",
                        "description": "Title or summary of the appointment.",
                    },
                    "location": {
                        "type": "string",
                        "description": "Optional location of the appointment.",
                    },
                },
                "required": ["date", "start", "end", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather forecast for a city and date (YYYY-MM-DD).",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "City slug in lowercase ASCII, e.g. 'ulm' or 'muenchen'.",
                    },
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format (e.g. '2026-10-29').",
                    },
                },
                "required": ["city", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_train_connections",
            "description": "Get train connections between two cities on a date (YYYY-MM-DD).",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {
                        "type": "string",
                        "description": "Departure city slug in lowercase ASCII, e.g. 'ulm'.",
                    },
                    "destination": {
                        "type": "string",
                        "description": "Arrival city slug in lowercase ASCII, e.g. 'muenchen'.",
                    },
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format (e.g. '2026-10-29').",
                    },
                },
                "required": ["origin", "destination", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_context",
            "description": "Get user context such as home city/default departure location when not specified by the user.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]


def _normalize_city(city: str) -> str:
    """Normalisiert Städtenamen für die Endpunkt-URLs (z. B. Munich/München -> muenchen)."""
    cleaned = city.strip().lower()
    aliases = {
        "munich": "muenchen",
        "münchen": "muenchen",
    }
    if cleaned in aliases:
        return aliases[cleaned]
    return (
        cleaned.replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )


def _fetch_json(url: str, not_found_msg: str) -> tuple[str, bool]:
    """Zentraler HTTP-GET-Wrapper mit sauberem 404- und Fehler-Handling."""
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.text, True
        elif response.status_code == 404:
            return json.dumps({"data": None, "message": not_found_msg}), True
        else:
            return json.dumps({"error": f"HTTP {response.status_code} for {url}"}), False
    except Exception as e:
        return json.dumps({"error": f"Request failed: {str(e)}"}), False


def get_calendar_events(base_url: str, date: str) -> tuple[str, bool]:
    """Liest Kalendereinträge für ein Datum (YYYY-MM-DD)."""
    if not date or not isinstance(date, str):
        return json.dumps({"error": "Missing or invalid 'date' (expected YYYY-MM-DD)."}), False
    url = f"{base_url.rstrip('/')}/calendar/{date.strip()}.json"
    return _fetch_json(url, f"No calendar data available for {date}.")


def create_appointment(
        base_url: str,
        date: str,
        start: str,
        end: str,
        title: str,
        location: str | None = None,
) -> tuple[str, bool]:
    """Schreibt einen neuen Termin als JSON-Zeile in var/calendar.jsonl."""
    if not all([date, start, end, title]):
        return json.dumps({"error": "Missing required fields (date, start, end, title)."}), False

    appointment = {
        "date": date.strip(),
        "start": start.strip(),
        "end": end.strip(),
        "title": title.strip(),
    }
    if location:
        appointment["location"] = location.strip()

    try:
        os.makedirs("var", exist_ok=True)
        calendar_path = os.path.join("var", "calendar.jsonl")
        with open(calendar_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(appointment, ensure_ascii=False) + "\n")

        return json.dumps({"status": "created", "appointment": appointment}, ensure_ascii=False), True
    except Exception as e:
        return json.dumps({"error": f"Failed to write appointment: {str(e)}"}), False


def get_weather(base_url: str, city: str, date: str) -> tuple[str, bool]:
    """Liest Wetterdaten für eine Stadt und ein Datum (YYYY-MM-DD)."""
    if not city or not date:
        return json.dumps({"error": "Missing 'city' or 'date' parameter."}), False
    city_slug = _normalize_city(city)
    url = f"{base_url.rstrip('/')}/weather/{city_slug}/{date.strip()}.json"
    return _fetch_json(url, f"No weather data available for '{city}' on {date}.")


def get_train_connections(base_url: str, origin: str, destination: str, date: str) -> tuple[str, bool]:
    """Liest Zugverbindungen zwischen Start- und Zielstadt an einem Datum (YYYY-MM-DD)."""
    if not origin or not destination or not date:
        return json.dumps({"error": "Missing 'origin', 'destination', or 'date' parameter."}), False
    from_slug = _normalize_city(origin)
    to_slug = _normalize_city(destination)
    url = f"{base_url.rstrip('/')}/transit/{from_slug}/{to_slug}/{date.strip()}.json"
    return _fetch_json(url, f"No train connections available from '{origin}' to '{destination}' on {date}.")


def get_user_context(base_url: str) -> tuple[str, bool]:
    """Liest den Nutzerkontext (z. B. Wohnort) aus context.json."""
    url = f"{base_url.rstrip('/')}/context.json"
    return _fetch_json(url, "No user context available.")


TOOL_REGISTRY = {
    "get_calendar_events": get_calendar_events,
    "create_appointment": create_appointment,
    "get_weather": get_weather,
    "get_train_connections": get_train_connections,
    "get_user_context": get_user_context,
}


def execute_tool(fn_name: str, fn_args: dict, base_url: str) -> tuple[str, bool]:
    """Führt das angeforderte Tool sicher aus und fängt Argument- oder Laufzeitfehler ab."""
    if fn_name not in TOOL_REGISTRY:
        return json.dumps({"error": f"Unknown tool '{fn_name}'."}), False

    try:
        return TOOL_REGISTRY[fn_name](base_url=base_url, **fn_args)
    except TypeError as e:
        return json.dumps({"error": f"Argument mismatch for '{fn_name}': {str(e)}"}), False
    except Exception as e:
        return json.dumps({"error": f"Tool execution error: {str(e)}"}), False