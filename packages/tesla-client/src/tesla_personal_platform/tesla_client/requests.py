"""Typed request models matching Tesla's current Fleet API schema."""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, cast

from tesla_personal_platform.tesla_client.models import JsonObject, JsonValue


class Payload(Protocol):
    """A request that can be serialized without exposing secrets in repr."""

    def to_payload(self) -> JsonObject: ...


class _DataclassPayload:
    def to_payload(self) -> JsonObject:
        return _compact(cast(dict[str, object], asdict(cast(Any, self))))


@dataclass(frozen=True, slots=True)
class FleetTelemetryField(_DataclassPayload):
    interval_seconds: int
    minimum_delta: int | float | None = None
    resend_interval_seconds: int | None = None
    include_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FleetTelemetryConfig(_DataclassPayload):
    hostname: str
    ca: str = field(repr=False)
    fields: dict[str, FleetTelemetryField] = field(repr=False)
    port: int = 443
    alert_types: tuple[Literal["service", "service-fix"], ...] = ()
    exp: int | None = None
    delivery_policy: str | None = None

    def to_payload(self) -> JsonObject:
        payload: JsonObject = {
            "hostname": self.hostname,
            "ca": self.ca,
            "fields": {name: config.to_payload() for name, config in self.fields.items()},
            "port": self.port,
        }
        if self.alert_types:
            payload["alert_types"] = list(self.alert_types)
        if self.exp is not None:
            payload["exp"] = self.exp
        if self.delivery_policy is not None:
            payload["delivery_policy"] = self.delivery_policy
        return payload


@dataclass(frozen=True, slots=True)
class FleetTelemetryConfigRequest(_DataclassPayload):
    vins: tuple[str, ...]
    config: FleetTelemetryConfig

    def to_payload(self) -> JsonObject:
        return {"vins": list(self.vins), "config": self.config.to_payload()}


@dataclass(frozen=True, slots=True)
class SignedCommandRequest(_DataclassPayload):
    routable_message: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class FleetTelemetryJWSRequest(_DataclassPayload):
    vins: tuple[str, ...]
    token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class InvitationRedeemRequest(_DataclassPayload):
    code: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ChargeScheduleRequest(_DataclassPayload):
    lat: float = field(repr=False)
    lon: float = field(repr=False)
    id: int
    days_of_week: str
    start_enabled: bool
    start_time: int
    end_enabled: bool
    end_time: int
    one_time: bool
    enabled: bool


@dataclass(frozen=True, slots=True)
class PreconditionScheduleRequest(_DataclassPayload):
    lat: float = field(repr=False)
    lon: float = field(repr=False)
    id: int
    days_of_week: str
    precondition_time: int
    one_time: bool
    enabled: bool


@dataclass(frozen=True, slots=True)
class NavigationRequest(_DataclassPayload):
    type: str
    value: JsonObject = field(repr=False)
    locale: str
    timestamp_ms: str


@dataclass(frozen=True, slots=True)
class NavigationWaypointRequest(_DataclassPayload):
    waypoints: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CalendarRequest(_DataclassPayload):
    calendar_data: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class PinRequest(_DataclassPayload):
    pin: str = field(repr=False)

    def __post_init__(self) -> None:
        if len(self.pin) != 4 or not self.pin.isdigit():
            raise ValueError("PIN must contain exactly four digits")


@dataclass(frozen=True, slots=True)
class SetPinRequest(_DataclassPayload):
    on: bool
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        if len(self.password) != 4 or not self.password.isdigit():
            raise ValueError("PIN must contain exactly four digits")


@dataclass(frozen=True, slots=True)
class BoolRequest(_DataclassPayload):
    on: bool


@dataclass(frozen=True, slots=True)
class EnableRequest(_DataclassPayload):
    enable: bool


@dataclass(frozen=True, slots=True)
class IntegerRequest(_DataclassPayload):
    value: int

    def to_payload(self) -> JsonObject:
        raise NotImplementedError("use a field-specific request model")


@dataclass(frozen=True, slots=True)
class ActuateTrunkRequest(_DataclassPayload):
    which_trunk: Literal["front", "rear"]


@dataclass(frozen=True, slots=True)
class AdjustVolumeRequest(_DataclassPayload):
    volume: float


@dataclass(frozen=True, slots=True)
class CoordinatesRequest(_DataclassPayload):
    lat: float = field(repr=False)
    lon: float = field(repr=False)


@dataclass(frozen=True, slots=True)
class NavigationGPSRequest(_DataclassPayload):
    lat: float = field(repr=False)
    lon: float = field(repr=False)
    order: int | None = None


@dataclass(frozen=True, slots=True)
class NavigationSuperchargerRequest(_DataclassPayload):
    id: int
    order: int | None = None


@dataclass(frozen=True, slots=True)
class ParentalSettingRequest(_DataclassPayload):
    setting: str
    enable: bool


@dataclass(frozen=True, slots=True)
class SpeedLimitRequest(_DataclassPayload):
    limit_mph: int


@dataclass(frozen=True, slots=True)
class AutoSeatClimateRequest(_DataclassPayload):
    auto_seat_position: int
    auto_climate_on: bool


@dataclass(frozen=True, slots=True)
class SoundRequest(_DataclassPayload):
    sound: int


@dataclass(frozen=True, slots=True)
class SeatCoolerRequest(_DataclassPayload):
    seat_position: int
    seat_cooler_level: int


@dataclass(frozen=True, slots=True)
class SeatHeaterRequest(_DataclassPayload):
    seat_position: int
    level: int


@dataclass(frozen=True, slots=True)
class LevelRequest(_DataclassPayload):
    level: int


@dataclass(frozen=True, slots=True)
class ScheduleIDRequest(_DataclassPayload):
    id: int


@dataclass(frozen=True, slots=True)
class SoftwareUpdateRequest(_DataclassPayload):
    offset_sec: int


@dataclass(frozen=True, slots=True)
class OnWithOverrideRequest(_DataclassPayload):
    on: bool
    manual_override: bool


@dataclass(frozen=True, slots=True)
class CabinOverheatRequest(_DataclassPayload):
    on: bool
    fan_only: bool


@dataclass(frozen=True, slots=True)
class ChargeLimitRequest(_DataclassPayload):
    percent: int


@dataclass(frozen=True, slots=True)
class ChargingAmpsRequest(_DataclassPayload):
    charging_amps: int


@dataclass(frozen=True, slots=True)
class ClimateKeeperRequest(_DataclassPayload):
    climate_keeper_mode: Literal[0, 1, 2, 3]


@dataclass(frozen=True, slots=True)
class CabinOverheatTemperatureRequest(_DataclassPayload):
    cop_temp: Literal[0, 1, 2]


@dataclass(frozen=True, slots=True)
class ScheduledChargingRequest(_DataclassPayload):
    enable: bool
    time: int


@dataclass(frozen=True, slots=True)
class ScheduledDepartureRequest(_DataclassPayload):
    enable: bool
    departure_time: int
    off_peak_charging_enabled: bool
    off_peak_charging_weekdays_only: bool
    preconditioning_enabled: bool
    end_off_peak_time: int


@dataclass(frozen=True, slots=True)
class TemperatureRequest(_DataclassPayload):
    driver_temp: float
    passenger_temp: float


@dataclass(frozen=True, slots=True)
class VehicleNameRequest(_DataclassPayload):
    vehicle_name: str


@dataclass(frozen=True, slots=True)
class SunRoofRequest(_DataclassPayload):
    state: Literal["stop", "close", "vent"]


@dataclass(frozen=True, slots=True)
class HomeLinkRequest(_DataclassPayload):
    lat: float = field(repr=False)
    lon: float = field(repr=False)
    token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class WindowControlRequest(_DataclassPayload):
    command: Literal["vent", "close"]
    lat: float = field(repr=False)
    lon: float = field(repr=False)


@dataclass(frozen=True, slots=True)
class ChargingHistoryQuery:
    vin: str | None = field(default=None, repr=False)
    start_time: datetime | None = None
    end_time: datetime | None = None
    page: int | None = None
    page_size: int | None = None
    sort_by: str | None = None
    sort_order: Literal["asc", "desc"] | None = None


@dataclass(frozen=True, slots=True)
class VehicleDataQuery:
    endpoints: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.endpoints:
            raise ValueError("vehicle_data requires at least one targeted endpoint")
        if any(not endpoint or "," in endpoint for endpoint in self.endpoints):
            raise ValueError("vehicle_data endpoint names must be non-empty identifiers")


def _compact(values: dict[str, object]) -> JsonObject:
    result: JsonObject = {}
    for key, value in values.items():
        if value is None or value == ():
            continue
        if isinstance(value, tuple):
            result[key] = list(value)
        elif isinstance(value, dict):
            result[key] = {
                str(item_key): _to_json(item_value) for item_key, item_value in value.items()
            }
        else:
            result[key] = _to_json(value)
    return result


def _to_json(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, tuple):
        return [_to_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_json(item) for key, item in value.items()}
    if hasattr(value, "to_payload"):
        return cast(Payload, value).to_payload()
    raise TypeError(f"unsupported request value type: {type(value).__name__}")
