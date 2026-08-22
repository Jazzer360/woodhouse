"""Narrow Fleet API client used by Tesla onboarding only."""

from collections.abc import Mapping, Sequence
from urllib.parse import urlencode

from tesla_personal_platform.tesla_client.errors import TeslaAPIError, TeslaReauthorizationRequired
from tesla_personal_platform.tesla_client.models import (
    FleetStatus,
    TeslaRegion,
    TeslaVehicle,
)
from tesla_personal_platform.tesla_client.transport import HttpResponse, HttpTransport

KNOWN_FLEET_API_BASE_URLS = frozenset(
    {
        "https://fleet-api.prd.na.vn.cloud.tesla.com",
        "https://fleet-api.prd.eu.vn.cloud.tesla.com",
        "https://fleet-api.prd.cn.vn.cloud.tesla.cn",
    }
)


class TeslaFleetClient:
    """Typed onboarding calls; broad endpoint coverage belongs to Phase 5."""

    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport

    def region(self, access_token: str, *, base_url: str) -> TeslaRegion:
        document = self._request_json(
            "GET",
            f"{normalize_base_url(base_url)}/api/1/users/region",
            access_token,
        )
        response = _response_mapping(document)
        region = _string(response, "region")
        discovered = response.get("fleet_api_base_url", response.get("base_url"))
        if not isinstance(discovered, str):
            raise TeslaAPIError(
                "Tesla region response is missing Fleet API base URL",
                category="invalid_payload",
            )
        return TeslaRegion(region=region, base_url=normalize_base_url(discovered))

    def list_vehicles(self, access_token: str, *, base_url: str) -> list[TeslaVehicle]:
        vehicles: list[TeslaVehicle] = []
        page = 1
        while True:
            query = urlencode({"page": page, "per_page": 100})
            document = self._request_json(
                "GET",
                f"{normalize_base_url(base_url)}/api/1/vehicles?{query}",
                access_token,
            )
            response = document.get("response")
            if not isinstance(response, Sequence) or isinstance(response, (str, bytes)):
                raise TeslaAPIError(
                    "Tesla vehicle list response is invalid",
                    category="invalid_payload",
                )
            for item in response:
                if not isinstance(item, Mapping):
                    raise TeslaAPIError(
                        "Tesla vehicle list contains an invalid record",
                        category="invalid_payload",
                    )
                vin = _string(item, "vin")
                vehicle_id = item.get("id_s", item.get("id"))
                if not isinstance(vehicle_id, (str, int)) or isinstance(vehicle_id, bool):
                    raise TeslaAPIError(
                        "Tesla vehicle record is missing its ID",
                        category="invalid_payload",
                    )
                display_name = item.get("display_name")
                state = item.get("state")
                vehicles.append(
                    TeslaVehicle(
                        vin=vin,
                        tesla_vehicle_id=str(vehicle_id),
                        display_name=display_name if isinstance(display_name, str) else None,
                        state=state if isinstance(state, str) else None,
                    )
                )

            pagination = document.get("pagination")
            if not isinstance(pagination, Mapping):
                break
            next_page = pagination.get("next")
            if next_page is None or next_page is False:
                break
            if isinstance(next_page, int) and not isinstance(next_page, bool):
                page = next_page
            else:
                page += 1
            if page > 100:
                raise TeslaAPIError(
                    "Tesla vehicle pagination exceeded safe bounds",
                    category="pagination_limit",
                )
        return vehicles

    def fleet_status(
        self,
        access_token: str,
        *,
        base_url: str,
        vins: Sequence[str],
    ) -> dict[str, FleetStatus]:
        if not vins:
            return {}
        document = self._request_json(
            "POST",
            f"{normalize_base_url(base_url)}/api/1/vehicles/fleet_status",
            access_token,
            json_body={"vins": list(vins)},
        )
        response = _response_mapping(document)
        vehicle_info = response.get("vehicle_info")
        if not isinstance(vehicle_info, Mapping):
            raise TeslaAPIError(
                "Tesla fleet status response is missing vehicle_info",
                category="invalid_payload",
            )
        paired_vins = _optional_string_set(response, "key_paired_vins")
        unpaired_vins = _optional_string_set(response, "unpaired_vins")
        if paired_vins is not None and unpaired_vins is not None:
            if paired_vins & unpaired_vins:
                raise TeslaAPIError(
                    "Tesla fleet status pairing lists overlap",
                    category="invalid_payload",
                )

        statuses: dict[str, FleetStatus] = {}
        for vin, raw in vehicle_info.items():
            if not isinstance(vin, str) or not isinstance(raw, Mapping):
                continue
            raw_copy = {str(key): value for key, value in raw.items()}
            key_paired: bool | None = None
            if paired_vins is not None and vin in paired_vins:
                key_paired = True
            elif unpaired_vins is not None and vin in unpaired_vins:
                key_paired = False
            statuses[vin] = FleetStatus(
                vin=vin,
                key_paired=key_paired,
                vehicle_command_protocol_required=_optional_bool(
                    raw, "vehicle_command_protocol_required"
                ),
                firmware_version=_optional_string(raw, "firmware_version"),
                fleet_telemetry_version=_optional_string(raw, "fleet_telemetry_version"),
                total_number_of_keys=_optional_int(raw, "total_number_of_keys"),
                raw=raw_copy,
            )
        return statuses

    def _request_json(
        self,
        method: str,
        url: str,
        access_token: str,
        *,
        json_body: object | None = None,
    ) -> Mapping[str, object]:
        response = self._transport.request(
            method,
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            json_body=json_body,
        )
        return _successful_json(response)


def normalize_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if normalized not in KNOWN_FLEET_API_BASE_URLS:
        raise TeslaAPIError("Tesla returned an unrecognized Fleet API base URL")
    return normalized


def _successful_json(response: HttpResponse) -> Mapping[str, object]:
    if response.status == 401:
        raise TeslaReauthorizationRequired(
            "Tesla access token was rejected",
            category="reauthorization_required",
            status_code=response.status,
        )
    if response.status < 200 or response.status >= 300:
        raise TeslaAPIError(
            f"Tesla Fleet API request failed with status {response.status}",
            category="http_error",
            status_code=response.status,
        )
    try:
        document = response.json()
    except (UnicodeDecodeError, ValueError) as error:
        raise TeslaAPIError(
            "Tesla Fleet API returned invalid JSON",
            category="invalid_json",
            status_code=response.status,
        ) from error
    if not isinstance(document, Mapping):
        raise TeslaAPIError(
            "Tesla Fleet API returned an invalid document",
            category="invalid_document",
            status_code=response.status,
        )
    return document


def _response_mapping(document: Mapping[str, object]) -> Mapping[str, object]:
    response = document.get("response")
    if not isinstance(response, Mapping):
        raise TeslaAPIError(
            "Tesla Fleet API response payload is invalid",
            category="invalid_payload",
        )
    return response


def _string(document: Mapping[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise TeslaAPIError(
            f"Tesla response is missing {key}",
            category="invalid_payload",
        )
    return value


def _optional_string(document: Mapping[str, object], key: str) -> str | None:
    value = document.get(key)
    return value if isinstance(value, str) else None


def _optional_bool(document: Mapping[str, object], key: str) -> bool | None:
    value = document.get(key)
    return value if isinstance(value, bool) else None


def _optional_int(document: Mapping[str, object], key: str) -> int | None:
    value = document.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_string_set(document: Mapping[str, object], key: str) -> frozenset[str] | None:
    value = document.get(key)
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TeslaAPIError(
            f"Tesla response contains invalid {key}",
            category="invalid_payload",
        )
    if any(not isinstance(item, str) or not item for item in value):
        raise TeslaAPIError(
            f"Tesla response contains invalid {key}",
            category="invalid_payload",
        )
    return frozenset(value)
