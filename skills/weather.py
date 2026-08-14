"""
Weather skill.

Uses Open-Meteo (https://open-meteo.com) — free, no API key required.
Two calls: geocode the city name, then pull current weather for those
coordinates.
"""

import requests

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Minimal WMO weather-code -> description map (Open-Meteo uses WMO codes)
WEATHER_CODES = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}


def get_weather(city: str) -> dict:
    """
    Returns a dict like:
        {"ok": True, "city": "Jaipur", "temp_c": 34.2, "condition": "clear sky"}
    or on failure:
        {"ok": False, "error": "..."}
    """
    try:
        geo = requests.get(GEOCODE_URL, params={"name": city, "count": 1}, timeout=10)
        geo.raise_for_status()
        results = geo.json().get("results")
        if not results:
            return {"ok": False, "error": f"Could not find a location named '{city}'."}

        loc = results[0]
        lat, lon = loc["latitude"], loc["longitude"]
        resolved_name = loc.get("name", city)

        fc = requests.get(
            FORECAST_URL,
            params={"latitude": lat, "longitude": lon, "current_weather": "true"},
            timeout=10,
        )
        fc.raise_for_status()
        current = fc.json().get("current_weather")
        if not current:
            return {"ok": False, "error": "Weather service returned no data."}

        code = current.get("weathercode")
        return {
            "ok": True,
            "city": resolved_name,
            "temp_c": current.get("temperature"),
            "windspeed_kmh": current.get("windspeed"),
            "condition": WEATHER_CODES.get(code, f"unknown (code {code})"),
        }

    except requests.RequestException as e:
        return {"ok": False, "error": f"Network error contacting weather service: {e}"}


# Tool schema the planner hands to the model. Keep this next to the
# function it describes so the two never drift apart.
# Shape follows Gemini's Interactions API function-declaration format:
# https://ai.google.dev/gemini-api/docs/function-calling
TOOL_SCHEMA = {
    "type": "function",
    "name": "get_weather",
    "description": "Get the current weather for a named city.",
    "parameters": {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "City name, e.g. 'Jaipur' or 'San Francisco'",
            }
        },
        "required": ["city"],
    },
}
