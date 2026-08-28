"""Complete typed personal-vehicle Tesla Fleet API client."""

import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from urllib.parse import quote, urlencode

from tesla_personal_platform.tesla_client.errors import (
    TeslaAPIError,
    TeslaReauthorizationRequired,
    TeslaTransportError,
)
from tesla_personal_platform.tesla_client.models import (
    BinaryDocument,
    FleetStatus,
    JsonObject,
    JsonValue,
    ListResponse,
    ObjectResponse,
    Pagination,
    TeslaRegion,
    TeslaVehicle,
    ValueResponse,
    VehicleData,
    json_object,
)
from tesla_personal_platform.tesla_client.observability import tesla_api_log_context
from tesla_personal_platform.tesla_client.requests import (
    ChargingHistoryQuery,
    FleetTelemetryConfigRequest,
    FleetTelemetryJWSRequest,
    InvitationRedeemRequest,
    SignedCommandRequest,
    VehicleDataQuery,
)
from tesla_personal_platform.tesla_client.transport import HttpResponse, HttpTransport
from tesla_personal_platform.tesla_client.vehicle_commands import VehicleCommands

KNOWN_FLEET_API_BASE_URLS = frozenset(
    {
        "https://fleet-api.prd.na.vn.cloud.tesla.com",
        "https://fleet-api.prd.eu.vn.cloud.tesla.com",
        "https://fleet-api.prd.cn.vn.cloud.tesla.cn",
    }
)
_RETRYABLE_READ_STATUSES = frozenset({429, 500, 502, 503, 504})
type QueryValue = str | int | float | bool | None


class TeslaFleetClient(VehicleCommands):
    """Typed Fleet API calls with no implicit wake-up or command retry."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        max_read_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_read_retries < 0 or max_read_retries > 5:
            raise ValueError("max_read_retries must be between zero and five")
        self._transport = transport
        self._max_read_retries = max_read_retries
        self._sleep = sleep

    # User endpoints

    def feature_config(self, access_token: str, *, base_url: str) -> ObjectResponse:
        return self._object("GET", base_url, "/api/1/users/feature_config", access_token)

    def me(self, access_token: str, *, base_url: str) -> ObjectResponse:
        return self._object("GET", base_url, "/api/1/users/me", access_token)

    def orders(self, access_token: str, *, base_url: str) -> ListResponse:
        return self._list("GET", base_url, "/api/1/users/orders", access_token)

    def region(self, access_token: str, *, base_url: str) -> TeslaRegion:
        document = self._request_json("GET", base_url, "/api/1/users/region", access_token)
        response = _response_mapping(document)
        region = _string(response, "region")
        discovered = response.get("fleet_api_base_url", response.get("base_url"))
        if not isinstance(discovered, str):
            raise TeslaAPIError(
                "Tesla region response is missing Fleet API base URL",
                category="invalid_payload",
            )
        return TeslaRegion(region=region, base_url=normalize_base_url(discovered))

    # Vehicle endpoints

    def drivers(self, access_token: str, *, base_url: str, vin: str) -> ListResponse:
        return self._list("GET", base_url, _vehicle_path(vin, "drivers"), access_token)

    def drivers_remove(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        share_user_id: int | None = None,
    ) -> ValueResponse:
        return self._value(
            "DELETE",
            base_url,
            _with_query(_vehicle_path(vin, "drivers"), {"share_user_id": share_user_id}),
            access_token,
        )

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
            base_url,
            "/api/1/vehicles/fleet_status",
            access_token,
            json_body={"vins": list(vins)},
            retry_safe=True,
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
        if paired_vins is not None and unpaired_vins is not None and paired_vins & unpaired_vins:
            raise TeslaAPIError(
                "Tesla fleet status pairing lists overlap",
                category="invalid_payload",
            )

        statuses: dict[str, FleetStatus] = {}
        for vin, raw in vehicle_info.items():
            if not isinstance(vin, str) or not isinstance(raw, Mapping):
                continue
            statuses[vin] = FleetStatus(
                vin=vin,
                key_paired=(
                    True
                    if paired_vins is not None and vin in paired_vins
                    else False
                    if unpaired_vins is not None and vin in unpaired_vins
                    else None
                ),
                vehicle_command_protocol_required=_optional_bool(
                    raw, "vehicle_command_protocol_required"
                ),
                firmware_version=_optional_string(raw, "firmware_version"),
                fleet_telemetry_version=_optional_string(raw, "fleet_telemetry_version"),
                total_number_of_keys=_optional_int(raw, "total_number_of_keys"),
                raw=json_object(raw),
            )
        return statuses

    def fleet_telemetry_config_create(
        self,
        access_token: str,
        *,
        base_url: str,
        request: FleetTelemetryConfigRequest,
    ) -> ObjectResponse:
        return self._object(
            "POST",
            base_url,
            "/api/1/vehicles/fleet_telemetry_config",
            access_token,
            json_body=request.to_payload(),
        )

    def fleet_telemetry_config_delete(
        self, access_token: str, *, base_url: str, vin: str
    ) -> ValueResponse:
        return self._value(
            "DELETE", base_url, _vehicle_path(vin, "fleet_telemetry_config"), access_token
        )

    def fleet_telemetry_config_get(
        self, access_token: str, *, base_url: str, vin: str
    ) -> ObjectResponse:
        return self._object(
            "GET", base_url, _vehicle_path(vin, "fleet_telemetry_config"), access_token
        )

    def fleet_telemetry_config_jws(
        self,
        access_token: str,
        *,
        base_url: str,
        request: FleetTelemetryJWSRequest,
    ) -> ObjectResponse:
        """Compatibility-only direct JWS path; prefer the command proxy create method."""

        return self._object(
            "POST",
            base_url,
            "/api/1/vehicles/fleet_telemetry_config_jws",
            access_token,
            json_body=request.to_payload(),
        )

    def fleet_telemetry_errors(
        self, access_token: str, *, base_url: str, vin: str
    ) -> ValueResponse:
        return self._value(
            "GET", base_url, _vehicle_path(vin, "fleet_telemetry_errors"), access_token
        )

    def list_vehicles(self, access_token: str, *, base_url: str) -> list[TeslaVehicle]:
        vehicles: list[TeslaVehicle] = []
        page = 1
        while True:
            document = self._request_json(
                "GET",
                base_url,
                _with_query("/api/1/vehicles", {"page": page, "per_page": 100}),
                access_token,
            )
            response = _response_sequence(document)
            for item in response:
                vehicles.append(_vehicle(item))

            pagination = _pagination(document)
            if pagination is None or pagination.next_page is None:
                break
            page = pagination.next_page
            if page > 100:
                raise TeslaAPIError(
                    "Tesla vehicle pagination exceeded safe bounds",
                    category="pagination_limit",
                )
        return vehicles

    def mobile_enabled(self, access_token: str, *, base_url: str, vin: str) -> ObjectResponse:
        return self._object("GET", base_url, _vehicle_path(vin, "mobile_enabled"), access_token)

    def nearby_charging_sites(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        count: int | None = None,
        radius: float | None = None,
        detail: bool | None = None,
    ) -> ObjectResponse:
        path = _with_query(
            _vehicle_path(vin, "nearby_charging_sites"),
            {"count": count, "radius": radius, "detail": detail},
        )
        return self._object("GET", base_url, path, access_token)

    def recent_alerts(self, access_token: str, *, base_url: str, vin: str) -> ValueResponse:
        return self._value("GET", base_url, _vehicle_path(vin, "recent_alerts"), access_token)

    def release_notes(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        staged: bool | None = None,
        language: str | None = None,
    ) -> ValueResponse:
        path = _with_query(
            _vehicle_path(vin, "release_notes"),
            {"staged": staged, "language": language},
        )
        return self._value("GET", base_url, path, access_token)

    def service_data(self, access_token: str, *, base_url: str, vin: str) -> ValueResponse:
        return self._value("GET", base_url, _vehicle_path(vin, "service_data"), access_token)

    def share_invites(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        page: int = 1,
        per_page: int = 25,
    ) -> ListResponse:
        path = _with_query(
            _vehicle_path(vin, "invitations"),
            {"page": page, "per_page": per_page},
        )
        return self._list("GET", base_url, path, access_token)

    def share_invites_create(self, access_token: str, *, base_url: str, vin: str) -> ObjectResponse:
        return self._object("POST", base_url, _vehicle_path(vin, "invitations"), access_token)

    def share_invites_redeem(
        self,
        access_token: str,
        *,
        base_url: str,
        request: InvitationRedeemRequest,
    ) -> ObjectResponse:
        return self._object(
            "POST",
            base_url,
            "/api/1/invitations/redeem",
            access_token,
            json_body=request.to_payload(),
        )

    def share_invites_revoke(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        invitation_id: str,
    ) -> ValueResponse:
        path = f"{_vehicle_path(vin, 'invitations')}/{_path_part(invitation_id)}/revoke"
        return self._value("POST", base_url, path, access_token)

    def signed_command(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: SignedCommandRequest,
    ) -> ValueResponse:
        """Internal Vehicle Command Protocol transport; never an MCP passthrough."""

        return self._value(
            "POST",
            base_url,
            _vehicle_path(vin, "signed_command"),
            access_token,
            json_body=request.to_payload(),
        )

    def vehicle(self, access_token: str, *, base_url: str, vin: str) -> TeslaVehicle:
        document = self._request_json("GET", base_url, _vehicle_path(vin), access_token)
        return _vehicle(_response_mapping(document))

    def vehicle_data(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        query: VehicleDataQuery,
    ) -> VehicleData:
        path = _with_query(
            _vehicle_path(vin, "vehicle_data"),
            {"endpoints": ",".join(query.endpoints)},
        )
        document = self._request_json("GET", base_url, path, access_token)
        response = _response_mapping(document)
        vehicle = _vehicle(response)
        identity_fields = {
            "id",
            "id_s",
            "vehicle_id",
            "vin",
            "display_name",
            "state",
        }
        sections = json_object(
            {key: value for key, value in response.items() if str(key) not in identity_fields}
        )
        return VehicleData(vehicle=vehicle, sections=sections)

    def wake_up(self, access_token: str, *, base_url: str, vin: str) -> TeslaVehicle:
        document = self._request_json("POST", base_url, _vehicle_path(vin, "wake_up"), access_token)
        return _vehicle(_response_mapping(document))

    # Charging endpoints

    def charging_history(
        self,
        access_token: str,
        *,
        base_url: str,
        query: ChargingHistoryQuery,
    ) -> ObjectResponse:
        path = _with_query(
            "/api/1/dx/charging/history",
            {
                "vin": query.vin,
                "startTime": _datetime(query.start_time),
                "endTime": _datetime(query.end_time),
                "pageNo": query.page,
                "pageSize": query.page_size,
                "sortBy": query.sort_by,
                "sortOrder": query.sort_order,
            },
        )
        return self._object("GET", base_url, path, access_token)

    def charging_invoice(
        self, access_token: str, *, base_url: str, invoice_id: str
    ) -> BinaryDocument:
        response = self._request(
            "GET",
            base_url,
            f"/api/1/dx/charging/invoice/{_path_part(invoice_id)}",
            access_token,
            retry_safe=True,
        )
        _raise_for_status(response)
        return BinaryDocument(
            content=response.body,
            content_type=response.content_type or "application/octet-stream",
        )

    def _object(
        self,
        method: str,
        base_url: str,
        path: str,
        access_token: str,
        *,
        json_body: JsonObject | None = None,
    ) -> ObjectResponse:
        document = self._request_json(
            method,
            base_url,
            path,
            access_token,
            json_body=json_body,
            retry_safe=method == "GET",
        )
        return ObjectResponse(json_object(_response_mapping(document)))

    def _list(self, method: str, base_url: str, path: str, access_token: str) -> ListResponse:
        document = self._request_json(
            method, base_url, path, access_token, retry_safe=method == "GET"
        )
        return ListResponse(
            tuple(json_object(item) for item in _response_sequence(document)),
            _pagination(document),
        )

    def _value(
        self,
        method: str,
        base_url: str,
        path: str,
        access_token: str,
        *,
        json_body: JsonObject | None = None,
    ) -> ValueResponse:
        document = self._request_json(
            method,
            base_url,
            path,
            access_token,
            json_body=json_body,
            retry_safe=method == "GET",
        )
        return ValueResponse(_response_value(document))

    def _request_json(
        self,
        method: str,
        base_url: str,
        path: str,
        access_token: str,
        *,
        json_body: JsonObject | None = None,
        retry_safe: bool = False,
    ) -> Mapping[str, object]:
        response = self._request(
            method,
            base_url,
            path,
            access_token,
            json_body=json_body,
            retry_safe=retry_safe,
        )
        return _successful_json(response)

    def _request(
        self,
        method: str,
        base_url: str,
        path: str,
        access_token: str,
        *,
        json_body: JsonObject | None = None,
        retry_safe: bool = False,
    ) -> HttpResponse:
        url = f"{normalize_base_url(base_url)}{path}"
        for attempt in range(self._max_read_retries + 1):
            try:
                with tesla_api_log_context(attempt=attempt + 1):
                    response = self._transport.request(
                        method,
                        url,
                        headers={"Authorization": f"Bearer {access_token}"},
                        json_body=json_body,
                    )
            except TeslaTransportError:
                if not retry_safe or attempt >= self._max_read_retries:
                    raise
                self._sleep(0.25 * (2**attempt))
                continue
            if (
                retry_safe
                and response.status in _RETRYABLE_READ_STATUSES
                and attempt < self._max_read_retries
            ):
                self._sleep(0.25 * (2**attempt))
                continue
            return response
        raise AssertionError("retry loop must return or raise")


def normalize_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if normalized not in KNOWN_FLEET_API_BASE_URLS:
        raise TeslaAPIError(
            "Tesla returned an unrecognized Fleet API base URL",
            category="invalid_region",
        )
    return normalized


def _successful_json(response: HttpResponse) -> Mapping[str, object]:
    _raise_for_status(response)
    try:
        document = response.json()
    except (UnicodeDecodeError, ValueError):
        raise TeslaAPIError(
            "Tesla Fleet API returned invalid JSON",
            category="invalid_json",
            status_code=response.status,
        ) from None
    if not isinstance(document, Mapping):
        raise TeslaAPIError(
            "Tesla Fleet API returned an invalid document",
            category="invalid_document",
            status_code=response.status,
        )
    return document


def _raise_for_status(response: HttpResponse) -> None:
    if response.status == 401:
        raise TeslaReauthorizationRequired(
            "Tesla access token was rejected",
            category="reauthorization_required",
            status_code=response.status,
        )
    if 200 <= response.status < 300:
        return
    category = {
        400: "invalid_request",
        403: "forbidden",
        404: "not_found",
        408: "vehicle_unavailable",
        409: "conflict",
        422: "invalid_request",
        429: "rate_limited",
    }.get(response.status, "upstream_unavailable" if response.status >= 500 else "http_error")
    raise TeslaAPIError(
        f"Tesla Fleet API request failed with status {response.status}",
        category=category,
        status_code=response.status,
    )


def _response_mapping(document: Mapping[str, object]) -> Mapping[str, object]:
    response = document.get("response")
    if not isinstance(response, Mapping):
        raise TeslaAPIError(
            "Tesla Fleet API response payload is invalid",
            category="invalid_payload",
        )
    return response


def _response_sequence(document: Mapping[str, object]) -> list[Mapping[str, object]]:
    response = document.get("response")
    if not isinstance(response, Sequence) or isinstance(response, (str, bytes)):
        raise TeslaAPIError(
            "Tesla Fleet API response list is invalid",
            category="invalid_payload",
        )
    if any(not isinstance(item, Mapping) for item in response):
        raise TeslaAPIError(
            "Tesla Fleet API response list contains an invalid record",
            category="invalid_payload",
        )
    return [item for item in response if isinstance(item, Mapping)]


def _response_value(document: Mapping[str, object]) -> JsonValue:
    if "response" not in document:
        raise TeslaAPIError(
            "Tesla Fleet API response payload is missing",
            category="invalid_payload",
        )
    value = document["response"]
    if isinstance(value, Mapping):
        return json_object(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [json_object(item) if isinstance(item, Mapping) else _scalar(item) for item in value]
    return _scalar(value)


def _scalar(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TeslaAPIError("Tesla response value is invalid", category="invalid_payload")


def _vehicle(document: Mapping[str, object]) -> TeslaVehicle:
    vin = _string(document, "vin")
    vehicle_id = document.get("id_s", document.get("id", document.get("vehicle_id")))
    if not isinstance(vehicle_id, (str, int)) or isinstance(vehicle_id, bool):
        raise TeslaAPIError("Tesla vehicle record is missing its ID", category="invalid_payload")
    return TeslaVehicle(
        vin=vin,
        tesla_vehicle_id=str(vehicle_id),
        display_name=_optional_string(document, "display_name"),
        state=_optional_string(document, "state"),
        raw=json_object(document),
    )


def _pagination(document: Mapping[str, object]) -> Pagination | None:
    value = document.get("pagination")
    if value is None:
        response = document.get("response")
        if isinstance(response, Mapping):
            value = response.get("pagination")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TeslaAPIError("Tesla pagination metadata is invalid", category="invalid_payload")
    return Pagination(
        current=_optional_int(value, "current"),
        per_page=_optional_int(value, "per_page"),
        count=_optional_int(value, "count"),
        pages=_optional_int(value, "pages"),
        next_page=_optional_int(value, "next"),
        previous_page=_optional_int(value, "previous"),
    )


def _vehicle_path(vin: str, suffix: str | None = None) -> str:
    path = f"/api/1/vehicles/{_path_part(vin)}"
    return f"{path}/{suffix}" if suffix else path


def _path_part(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Tesla path identifier cannot be empty")
    return quote(normalized, safe="")


def _with_query(path: str, values: Mapping[str, QueryValue]) -> str:
    populated = {key: value for key, value in values.items() if value is not None}
    return f"{path}?{urlencode(populated)}" if populated else path


def _datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _string(document: Mapping[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise TeslaAPIError(f"Tesla response is missing {key}", category="invalid_payload")
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
        raise TeslaAPIError(f"Tesla response contains invalid {key}", category="invalid_payload")
    if any(not isinstance(item, str) or not item for item in value):
        raise TeslaAPIError(f"Tesla response contains invalid {key}", category="invalid_payload")
    return frozenset(value)
