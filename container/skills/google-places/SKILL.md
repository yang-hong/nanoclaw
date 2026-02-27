---
name: google-places
description: Search for places, restaurants, shops, landmarks — get ratings, reviews, opening hours, addresses, and detailed place info using Google Places API. Use when the user asks about nearby places, recommendations, or anything location-related.
allowed-tools: Bash(curl:*)
---

# Google Places Search

Use the Google Places API (New) to find places and get detailed information.
The API key is available as `$GOOGLE_API_KEY` in your environment.

## Default location

Owner's base location (use as default for "nearby", "near me", "附近", etc.):
- **Latitude:** 37.3530
- **Longitude:** -122.1033
- **Area:** Los Altos / Sunnyvale, CA

Always include `locationBias` with these coordinates unless the user specifies a different location. Do NOT ask the user where they are.

## Text Search (most versatile)

Search for places using natural language. Always include `locationBias` with the default coordinates:

```bash
curl -s -X POST "https://places.googleapis.com/v1/places:searchText" \
  -H "Content-Type: application/json" \
  -H "X-Goog-Api-Key: $GOOGLE_API_KEY" \
  -H "X-Goog-FieldMask: places.id,places.displayName,places.formattedAddress,places.location,places.rating,places.userRatingCount,places.currentOpeningHours.openNow,places.priceLevel,places.types,places.websiteUri,places.nationalPhoneNumber,places.editorialSummary,places.googleMapsUri" \
  -d '{
    "textQuery": "best sushi restaurants",
    "locationBias": {
      "circle": {
        "center": { "latitude": 37.3530, "longitude": -122.1033 },
        "radius": 5000.0
      }
    },
    "openNow": true,
    "maxResultCount": 10
  }'
```

### Key parameters

- `textQuery`: Natural language query (required)
- `openNow`: `true` to filter to currently open places
- `maxResultCount`: 1–20 (default 20)
- `rankPreference`: `"RELEVANCE"` (default) or `"DISTANCE"`
- `minRating`: Minimum rating filter (e.g. `4.0`)
- `priceLevels`: Array of `"PRICE_LEVEL_FREE"`, `"PRICE_LEVEL_INEXPENSIVE"`, `"PRICE_LEVEL_MODERATE"`, `"PRICE_LEVEL_EXPENSIVE"`, `"PRICE_LEVEL_VERY_EXPENSIVE"`
- `languageCode`: e.g. `"zh-CN"`, `"en"`, `"ja"`

### Custom location bias (override default)

If the user specifies a different area, override the default coordinates:

```json
{
  "textQuery": "coffee shops",
  "locationBias": {
    "circle": {
      "center": { "latitude": 37.7749, "longitude": -122.4194 },
      "radius": 2000.0
    }
  }
}
```

### Location restriction (strict area boundary)

```json
{
  "textQuery": "parking",
  "locationRestriction": {
    "rectangle": {
      "low": { "latitude": 37.35, "longitude": -122.15 },
      "high": { "latitude": 37.40, "longitude": -122.05 }
    }
  }
}
```

## Nearby Search (radius-based)

Find places by type within a radius (uses default location):

```bash
curl -s -X POST "https://places.googleapis.com/v1/places:searchNearby" \
  -H "Content-Type: application/json" \
  -H "X-Goog-Api-Key: $GOOGLE_API_KEY" \
  -H "X-Goog-FieldMask: places.id,places.displayName,places.formattedAddress,places.location,places.rating,places.userRatingCount,places.types,places.currentOpeningHours.openNow,places.priceLevel,places.googleMapsUri" \
  -d '{
    "includedTypes": ["restaurant"],
    "maxResultCount": 10,
    "locationRestriction": {
      "circle": {
        "center": { "latitude": 37.3530, "longitude": -122.1033 },
        "radius": 3000.0
      }
    }
  }'
```

Common `includedTypes`: `restaurant`, `cafe`, `bar`, `gas_station`, `pharmacy`, `hospital`, `supermarket`, `bank`, `atm`, `parking`, `park`, `gym`, `hotel`, `shopping_mall`, `movie_theater`, `museum`, `library`, `school`, `airport`, `train_station`, `bus_station`, `ev_charging_station`

## Place Details (by place ID)

Get full details for a specific place:

```bash
PLACE_ID="ChIJ..."  # from search results
curl -s "https://places.googleapis.com/v1/places/${PLACE_ID}" \
  -H "X-Goog-Api-Key: $GOOGLE_API_KEY" \
  -H "X-Goog-FieldMask: displayName,formattedAddress,rating,userRatingCount,currentOpeningHours,regularOpeningHours,websiteUri,nationalPhoneNumber,editorialSummary,reviews,priceLevel,googleMapsUri"
```

## Autocomplete (for partial input)

```bash
curl -s -X POST "https://places.googleapis.com/v1/places:autocomplete" \
  -H "Content-Type: application/json" \
  -H "X-Goog-Api-Key: $GOOGLE_API_KEY" \
  -d '{
    "input": "pizza near sunnyv",
    "locationBias": {
      "circle": {
        "center": { "latitude": 37.3688, "longitude": -122.0363 },
        "radius": 5000.0
      }
    }
  }'
```

## FieldMask Reference

Control which fields are returned (affects billing). Common fields:

| Field | Description |
|-------|-------------|
| `places.displayName` | Place name |
| `places.formattedAddress` | Full address |
| `places.rating` | Average rating (1-5) |
| `places.userRatingCount` | Number of reviews |
| `places.currentOpeningHours.openNow` | Currently open? |
| `places.regularOpeningHours` | Weekly hours |
| `places.priceLevel` | Price tier |
| `places.types` | Place categories |
| `places.websiteUri` | Website URL |
| `places.nationalPhoneNumber` | Phone number |
| `places.editorialSummary` | Short description |
| `places.googleMapsUri` | Google Maps link |
| `places.reviews` | User reviews |
| `places.location` | Lat/lng coordinates |
| `places.id` | Place ID (for details lookup) |

## Generating navigation links from search results

After finding a place, build a Google Maps navigation link for the user to tap:

```
https://www.google.com/maps/dir/?api=1&destination=PLACE_NAME&destination_place_id=PLACE_ID&travelmode=driving
```

Use the `places.id` field (strip the `places/` prefix) as `destination_place_id`.
Use the `places.displayName.text` + city as the `destination` (URL-encoded with `+`).

Example: if search returns `id: "places/ChIJN1t_tDeuEmsR"`, `displayName: "Dishdash"`:

```
https://www.google.com/maps/dir/?api=1&destination=Dishdash+Sunnyvale+CA&destination_place_id=ChIJN1t_tDeuEmsR&travelmode=driving
```

The user taps this in WhatsApp → Google Maps opens → navigation starts from their current location.

See the `google-navigation` skill for multi-stop routes and travel time estimates.

## Tips

- Always include `places.id` and `places.location` — needed for navigation links
- Use `openNow: true` when the user asks for "open now" or "currently open"
- For "best" or "top rated", sort results by rating in your output
- Always provide a Google Maps navigation link alongside each result
- When presenting results, format them cleanly for WhatsApp (no markdown headings)
