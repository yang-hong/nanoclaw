---
name: google-navigation
description: Generate Google Maps navigation links so the user can tap to start turn-by-turn navigation. Also estimates travel time/distance. Use when the user wants to go somewhere, asks "how do I get to", "navigate to", or needs directions — including multi-stop routes.
allowed-tools: Bash(curl:*)
---

# Google Navigation

Generate clickable Google Maps links for the user to start navigation.
The API key is available as `$GOOGLE_API_KEY` in your environment.

## Default location

Owner's base location (use as default origin for Routes API time estimates):
- **Latitude:** 37.3530
- **Longitude:** -122.1033
- **Area:** Los Altos / Sunnyvale, CA

For Google Maps navigation links, do NOT include an origin — this lets the phone use GPS for the most accurate starting point. Only use the default coordinates as origin when calling the Routes API to estimate travel time.

## Primary workflow: Google Maps navigation links

The user taps the link in WhatsApp → Google Maps opens → navigation starts from their current location.

### Simple navigation (current location → destination)

No API call needed. Construct the URL directly:

```
https://www.google.com/maps/dir/?api=1&destination=DESTINATION&travelmode=driving
```

Examples:

```
https://www.google.com/maps/dir/?api=1&destination=San+Francisco+Airport&travelmode=driving
https://www.google.com/maps/dir/?api=1&destination=37.6213,-122.3790&travelmode=driving
```

When you have a Place ID from a google-places search, use it for precision:

```
https://www.google.com/maps/dir/?api=1&destination=Dishdash+Sunnyvale&destination_place_id=ChIJN1t_tDeuEmsRUsoyG83frY4&travelmode=driving
```

### Navigation with waypoints (multi-stop)

```
https://www.google.com/maps/dir/?api=1&destination=FINAL_DEST&waypoints=STOP1|STOP2|STOP3&travelmode=driving
```

Example — drive from current location, stop at Costco, then go to SFO:

```
https://www.google.com/maps/dir/?api=1&destination=San+Francisco+International+Airport&waypoints=Costco+Sunnyvale+CA&travelmode=driving
```

Example — multiple stops:

```
https://www.google.com/maps/dir/?api=1&destination=San+Jose+Airport&waypoints=Philz+Coffee+Sunnyvale|Trader+Joes+Mountain+View&travelmode=driving
```

### With explicit origin (not current location)

```
https://www.google.com/maps/dir/?api=1&origin=Sunnyvale+CA&destination=San+Francisco+Airport&travelmode=driving
```

### Travel mode options

| Mode | URL value | When to use |
|------|-----------|-------------|
| Driving | `travelmode=driving` | Default, most common |
| Walking | `travelmode=walking` | Short distances, pedestrian |
| Cycling | `travelmode=bicycling` | Bike routes |
| Transit | `travelmode=transit` | Bus, train, subway |

### URL encoding rules

- Replace spaces with `+` or `%20`
- Coordinates use `LAT,LNG` format (no spaces)
- Waypoints are separated by `|` (`%7C` if encoded)
- Place IDs from Google Places API can be used for `destination_place_id`

## Typical flow: fuzzy search → navigate

When the user says something vague like "I want to eat sushi" or "find me a gas station":

1. Use the `google-places` skill to search and find matching places
2. Present the top results with ratings and info
3. Once the user picks one (or if there's an obvious best match), generate the Maps navigation link

Example output for WhatsApp:

```
Found these sushi places near you:

• *Sushi Tomi* ⭐ 4.8 (2,130 reviews) — 635 W Dana St, Mountain View
  Currently open · $$$

• *Sushi Sam's* ⭐ 4.6 (890 reviews) — 218 E 3rd Ave, San Mateo
  Currently open · $$

Tap to navigate:
🗺 Sushi Tomi: https://www.google.com/maps/dir/?api=1&destination=Sushi+Tomi+Mountain+View+CA&destination_place_id=ChIJ...&travelmode=driving
🗺 Sushi Sam's: https://www.google.com/maps/dir/?api=1&destination=Sushi+Sams+San+Mateo+CA&destination_place_id=ChIJ...&travelmode=driving
```

## Secondary: travel time estimates (Routes API)

Use the Routes API when the user asks "how long will it take" or "how far is it" — not for navigation links.

```bash
curl -s -X POST "https://routes.googleapis.com/directions/v2:computeRoutes" \
  -H "Content-Type: application/json" \
  -H "X-Goog-Api-Key: $GOOGLE_API_KEY" \
  -H "X-Goog-FieldMask: routes.duration,routes.distanceMeters,routes.legs.duration,routes.legs.distanceMeters,routes.travelAdvisory" \
  -d '{
    "origin": {
      "location": {
        "latLng": { "latitude": 37.3530, "longitude": -122.1033 }
      }
    },
    "destination": { "address": "San Francisco Airport" },
    "travelMode": "DRIVE",
    "routingPreference": "TRAFFIC_AWARE"
  }'
```

### With waypoints

```bash
curl -s -X POST "https://routes.googleapis.com/directions/v2:computeRoutes" \
  -H "Content-Type: application/json" \
  -H "X-Goog-Api-Key: $GOOGLE_API_KEY" \
  -H "X-Goog-FieldMask: routes.duration,routes.distanceMeters,routes.legs.duration,routes.legs.distanceMeters" \
  -d '{
    "origin": {
      "location": {
        "latLng": { "latitude": 37.3530, "longitude": -122.1033 }
      }
    },
    "destination": { "address": "San Francisco, CA" },
    "intermediates": [
      { "address": "Mountain View, CA" },
      { "address": "Palo Alto, CA" }
    ],
    "travelMode": "DRIVE",
    "routingPreference": "TRAFFIC_AWARE"
  }'
```

### Route matrix (compare multiple destinations)

When the user is deciding between places, compare drive times from default location:

```bash
curl -s -X POST "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix" \
  -H "Content-Type: application/json" \
  -H "X-Goog-Api-Key: $GOOGLE_API_KEY" \
  -H "X-Goog-FieldMask: originIndex,destinationIndex,duration,distanceMeters,status" \
  -d '{
    "origins": [
      {
        "waypoint": {
          "location": {
            "latLng": { "latitude": 37.3530, "longitude": -122.1033 }
          }
        }
      }
    ],
    "destinations": [
      { "waypoint": { "address": "SFO Airport" } },
      { "waypoint": { "address": "SJC Airport" } }
    ],
    "travelMode": "DRIVE",
    "routingPreference": "TRAFFIC_AWARE"
  }'
```

## Geocoding (address ↔ coordinates)

```bash
# Address → coordinates
curl -s "https://maps.googleapis.com/maps/api/geocode/json?address=1600+Amphitheatre+Parkway,+Mountain+View,+CA&key=$GOOGLE_API_KEY"

# Coordinates → address
curl -s "https://maps.googleapis.com/maps/api/geocode/json?latlng=37.4224764,-122.0842499&key=$GOOGLE_API_KEY"
```

## Tips

- **Always prefer Maps links** over raw route data — the user wants to tap and navigate
- When no origin is specified in the Maps URL, Google Maps uses the phone's current location automatically
- Duration from Routes API is in seconds (e.g. "2514s") — convert to minutes/hours
- Distance is in meters — convert to miles (÷ 1609) for US users
- Use `TRAFFIC_AWARE` for realistic drive time estimates
- Always mention tolls if the route has them
- For multi-stop trips, present the waypoints in order and the total estimated time
