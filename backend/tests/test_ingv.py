"""INGV, against GeoJSON its FDSN event service answered on 03.11.2026.

Three quakes of magnitude 2 or more within 400 km of Milan; the 204 and the
400 are what the service gave for a magnitude no quake reached and for one
that was not a number.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, Context
from app.adapters.ingv import API, _when

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ingv_events.json").read_text())
NEAR_MILAN = {"latitude": 45.46, "longitude": 9.19, "radius_km": 400, "min_magnitude": 2, "days": 10}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={})


def test_the_time_without_a_zone_is_utc() -> None:
    assert _when("2026-11-02T00:51:05.410000") == datetime(2026, 11, 2, 0, 51, 5, 410000, tzinfo=UTC).timestamp()


@respx.mock
async def test_the_latest_with_depth_distance_and_magnitude() -> None:
    route = respx.get(API).mock(return_value=httpx.Response(200, json=FIXTURE["near_milan"]))
    data = await get_adapter("ingv").fetch("latest", {}, NEAR_MILAN, _ctx())
    asked = route.calls[0].request.url.params
    assert (asked["lat"], asked["lon"], asked["maxradiuskm"], asked["minmag"], asked["format"]) == ("45.46", "9.19", "400", "2.0", "geojson")
    first = data.items[0]
    assert first["title"] == "4 km SE Morfasso (PC)" and first["value"] == "M2.3" and first["status"] == "ok"
    assert first["subtitle"].startswith("25 km deep · 96 km away · ")
    assert data.status == "ok"


@respx.mock
async def test_without_a_place_the_whole_country_is_asked() -> None:
    route = respx.get(API).mock(return_value=httpx.Response(200, json=FIXTURE["near_milan"]))
    data = await get_adapter("ingv").fetch("latest", {}, {"min_magnitude": 2}, _ctx())
    assert "lat" not in route.calls[0].request.url.params
    assert "km away" not in data.items[0]["subtitle"]


@respx.mock
async def test_no_quake_is_204_and_an_empty_card() -> None:
    respx.get(API).mock(return_value=httpx.Response(204))
    data = await get_adapter("ingv").fetch("summary", {}, {"min_magnitude": 9}, _ctx())
    assert data.primary == {"label": "Earthquakes", "value": 0} and data.status == "ok"


@respx.mock
async def test_a_strong_quake_turns_the_card_red() -> None:
    strong = json.loads(json.dumps(FIXTURE["near_milan"]))
    strong["features"][1]["properties"]["mag"] = 5.4
    respx.get(API).mock(return_value=httpx.Response(200, json=strong))
    data = await get_adapter("ingv").fetch("summary", {}, NEAR_MILAN, _ctx())
    assert data.status == "bad" and data.secondary[0] == {"label": "Strongest", "value": "M5.4"}


@respx.mock
async def test_a_question_it_does_not_understand_is_said() -> None:
    respx.get(API).mock(return_value=httpx.Response(400, text="Error 400: Bad Request\n\nSyntax Error in Request"))
    with pytest.raises(AdapterError) as refused:
        await get_adapter("ingv").fetch("latest", {}, NEAR_MILAN, _ctx())
    assert refused.value.code == "bad_request"
