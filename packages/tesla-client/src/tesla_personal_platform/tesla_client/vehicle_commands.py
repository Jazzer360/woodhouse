"""Typed vehicle-command domain for the Tesla Fleet API client."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from urllib.parse import quote

from tesla_personal_platform.tesla_client import requests as command_requests
from tesla_personal_platform.tesla_client.errors import TeslaAPIError
from tesla_personal_platform.tesla_client.models import CommandResult, JsonObject, json_object


class VehicleCommands(ABC):
    """Explicit command methods shared by the Fleet API facade.

    Implementations provide ``_request_json``. Commands never wake a vehicle and
    never identify themselves as retry-safe.
    """

    def actuate_trunk(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.ActuateTrunkRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "actuate_trunk", request)

    def add_charge_schedule(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.ChargeScheduleRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "add_charge_schedule", request)

    def add_precondition_schedule(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.PreconditionScheduleRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "add_precondition_schedule", request)

    def adjust_volume(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.AdjustVolumeRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "adjust_volume", request)

    def auto_conditioning_start(
        self, access_token: str, *, base_url: str, vin: str
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "auto_conditioning_start")

    def auto_conditioning_stop(
        self, access_token: str, *, base_url: str, vin: str
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "auto_conditioning_stop")

    def cancel_software_update(
        self, access_token: str, *, base_url: str, vin: str
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "cancel_software_update")

    def charge_max_range(self, access_token: str, *, base_url: str, vin: str) -> CommandResult:
        return self._command(access_token, base_url, vin, "charge_max_range")

    def charge_port_door_close(
        self, access_token: str, *, base_url: str, vin: str
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "charge_port_door_close")

    def charge_port_door_open(self, access_token: str, *, base_url: str, vin: str) -> CommandResult:
        return self._command(access_token, base_url, vin, "charge_port_door_open")

    def charge_standard(self, access_token: str, *, base_url: str, vin: str) -> CommandResult:
        return self._command(access_token, base_url, vin, "charge_standard")

    def charge_start(self, access_token: str, *, base_url: str, vin: str) -> CommandResult:
        return self._command(access_token, base_url, vin, "charge_start")

    def charge_stop(self, access_token: str, *, base_url: str, vin: str) -> CommandResult:
        return self._command(access_token, base_url, vin, "charge_stop")

    def clear_pin_to_drive_admin(
        self, access_token: str, *, base_url: str, vin: str
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "clear_pin_to_drive_admin")

    def door_lock(self, access_token: str, *, base_url: str, vin: str) -> CommandResult:
        return self._command(access_token, base_url, vin, "door_lock")

    def door_unlock(self, access_token: str, *, base_url: str, vin: str) -> CommandResult:
        return self._command(access_token, base_url, vin, "door_unlock")

    def erase_user_data(self, access_token: str, *, base_url: str, vin: str) -> CommandResult:
        return self._command(access_token, base_url, vin, "erase_user_data")

    def flash_lights(self, access_token: str, *, base_url: str, vin: str) -> CommandResult:
        return self._command(access_token, base_url, vin, "flash_lights")

    def guest_mode(
        self, access_token: str, *, base_url: str, vin: str, request: command_requests.EnableRequest
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "guest_mode", request)

    def honk_horn(self, access_token: str, *, base_url: str, vin: str) -> CommandResult:
        return self._command(access_token, base_url, vin, "honk_horn")

    def media_next_fav(self, access_token: str, *, base_url: str, vin: str) -> CommandResult:
        return self._command(access_token, base_url, vin, "media_next_fav")

    def media_next_track(self, access_token: str, *, base_url: str, vin: str) -> CommandResult:
        return self._command(access_token, base_url, vin, "media_next_track")

    def media_prev_fav(self, access_token: str, *, base_url: str, vin: str) -> CommandResult:
        return self._command(access_token, base_url, vin, "media_prev_fav")

    def media_prev_track(self, access_token: str, *, base_url: str, vin: str) -> CommandResult:
        return self._command(access_token, base_url, vin, "media_prev_track")

    def media_toggle_playback(self, access_token: str, *, base_url: str, vin: str) -> CommandResult:
        return self._command(access_token, base_url, vin, "media_toggle_playback")

    def media_volume_down(self, access_token: str, *, base_url: str, vin: str) -> CommandResult:
        return self._command(access_token, base_url, vin, "media_volume_down")

    def media_volume_up(self, access_token: str, *, base_url: str, vin: str) -> CommandResult:
        return self._command(access_token, base_url, vin, "media_volume_up")

    def navigation_gps_request(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.NavigationGPSRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "navigation_gps_request", request)

    def navigation_request(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.NavigationRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "navigation_request", request)

    def navigation_sc_request(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.NavigationSuperchargerRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "navigation_sc_request", request)

    def navigation_waypoints_request(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.NavigationWaypointRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "navigation_waypoints_request", request)

    def parental_controls_activate(
        self, access_token: str, *, base_url: str, vin: str, request: command_requests.PinRequest
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "parental_controls_activate", request)

    def parental_controls_clear_pin_admin(
        self, access_token: str, *, base_url: str, vin: str
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "parental_controls_clear_pin_admin")

    def parental_controls_deactivate(
        self, access_token: str, *, base_url: str, vin: str, request: command_requests.PinRequest
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "parental_controls_deactivate", request)

    def parental_controls_enable_setting(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.ParentalSettingRequest,
    ) -> CommandResult:
        return self._command(
            access_token, base_url, vin, "parental_controls_enable_setting", request
        )

    def parental_controls_set_speed_limit(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.SpeedLimitRequest,
    ) -> CommandResult:
        return self._command(
            access_token, base_url, vin, "parental_controls_set_speed_limit", request
        )

    def remote_auto_seat_climate_request(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.AutoSeatClimateRequest,
    ) -> CommandResult:
        return self._command(
            access_token, base_url, vin, "remote_auto_seat_climate_request", request
        )

    def remote_auto_steering_wheel_heat_climate_request(
        self, access_token: str, *, base_url: str, vin: str, request: command_requests.BoolRequest
    ) -> CommandResult:
        return self._command(
            access_token, base_url, vin, "remote_auto_steering_wheel_heat_climate_request", request
        )

    def remote_boombox(
        self, access_token: str, *, base_url: str, vin: str, request: command_requests.SoundRequest
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "remote_boombox", request)

    def remote_seat_cooler_request(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.SeatCoolerRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "remote_seat_cooler_request", request)

    def remote_seat_heater_request(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.SeatHeaterRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "remote_seat_heater_request", request)

    def remote_start_drive(self, access_token: str, *, base_url: str, vin: str) -> CommandResult:
        return self._command(access_token, base_url, vin, "remote_start_drive")

    def remote_steering_wheel_heat_level_request(
        self, access_token: str, *, base_url: str, vin: str, request: command_requests.LevelRequest
    ) -> CommandResult:
        return self._command(
            access_token, base_url, vin, "remote_steering_wheel_heat_level_request", request
        )

    def remote_steering_wheel_heater_request(
        self, access_token: str, *, base_url: str, vin: str, request: command_requests.BoolRequest
    ) -> CommandResult:
        return self._command(
            access_token, base_url, vin, "remote_steering_wheel_heater_request", request
        )

    def remove_charge_schedule(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.ScheduleIDRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "remove_charge_schedule", request)

    def remove_precondition_schedule(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.ScheduleIDRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "remove_precondition_schedule", request)

    def reset_pin_to_drive_pin(
        self, access_token: str, *, base_url: str, vin: str
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "reset_pin_to_drive_pin")

    def reset_valet_pin(self, access_token: str, *, base_url: str, vin: str) -> CommandResult:
        return self._command(access_token, base_url, vin, "reset_valet_pin")

    def schedule_software_update(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.SoftwareUpdateRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "schedule_software_update", request)

    def set_bioweapon_mode(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.OnWithOverrideRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "set_bioweapon_mode", request)

    def set_cabin_overheat_protection(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.CabinOverheatRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "set_cabin_overheat_protection", request)

    def set_charge_limit(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.ChargeLimitRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "set_charge_limit", request)

    def set_charging_amps(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.ChargingAmpsRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "set_charging_amps", request)

    def set_climate_keeper_mode(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.ClimateKeeperRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "set_climate_keeper_mode", request)

    def set_cop_temp(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.CabinOverheatTemperatureRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "set_cop_temp", request)

    def set_pin_to_drive(
        self, access_token: str, *, base_url: str, vin: str, request: command_requests.SetPinRequest
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "set_pin_to_drive", request)

    def set_preconditioning_max(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.OnWithOverrideRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "set_preconditioning_max", request)

    def set_scheduled_charging(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.ScheduledChargingRequest,
    ) -> CommandResult:
        """Compatibility-only; add_charge_schedule is preferred on current firmware."""
        return self._command(access_token, base_url, vin, "set_scheduled_charging", request)

    def set_scheduled_departure(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.ScheduledDepartureRequest,
    ) -> CommandResult:
        """Compatibility-only; add_precondition_schedule is preferred."""
        return self._command(access_token, base_url, vin, "set_scheduled_departure", request)

    def set_sentry_mode(
        self, access_token: str, *, base_url: str, vin: str, request: command_requests.BoolRequest
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "set_sentry_mode", request)

    def set_temps(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.TemperatureRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "set_temps", request)

    def set_valet_mode(
        self, access_token: str, *, base_url: str, vin: str, request: command_requests.SetPinRequest
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "set_valet_mode", request)

    def set_vehicle_name(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.VehicleNameRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "set_vehicle_name", request)

    def speed_limit_activate(
        self, access_token: str, *, base_url: str, vin: str, request: command_requests.PinRequest
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "speed_limit_activate", request)

    def speed_limit_clear_pin(
        self, access_token: str, *, base_url: str, vin: str, request: command_requests.PinRequest
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "speed_limit_clear_pin", request)

    def speed_limit_clear_pin_admin(
        self, access_token: str, *, base_url: str, vin: str
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "speed_limit_clear_pin_admin")

    def speed_limit_deactivate(
        self, access_token: str, *, base_url: str, vin: str, request: command_requests.PinRequest
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "speed_limit_deactivate", request)

    def speed_limit_set_limit(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.SpeedLimitRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "speed_limit_set_limit", request)

    def sun_roof_control(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.SunRoofRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "sun_roof_control", request)

    def trigger_homelink(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.HomeLinkRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "trigger_homelink", request)

    def upcoming_calendar_entries(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.CalendarRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "upcoming_calendar_entries", request)

    def window_control(
        self,
        access_token: str,
        *,
        base_url: str,
        vin: str,
        request: command_requests.WindowControlRequest,
    ) -> CommandResult:
        return self._command(access_token, base_url, vin, "window_control", request)

    def _command(
        self,
        access_token: str,
        base_url: str,
        vin: str,
        name: str,
        request: command_requests.Payload | None = None,
    ) -> CommandResult:
        document = self._request_json(
            "POST",
            base_url,
            f"/api/1/vehicles/{_path_part(vin)}/command/{name}",
            access_token,
            json_body=request.to_payload() if request is not None else {},
        )
        response = document.get("response")
        if not isinstance(response, Mapping):
            raise TeslaAPIError(
                "Tesla Fleet API response payload is invalid", category="invalid_payload"
            )
        result = response.get("result")
        if not isinstance(result, bool):
            raise TeslaAPIError(
                "Tesla command response is missing result", category="invalid_payload"
            )
        reason = response.get("reason")
        return CommandResult(
            successful=result,
            reason=reason if isinstance(reason, str) and reason else None,
            raw=json_object(response),
        )

    @abstractmethod
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
        """Send one Fleet API request using the facade's guarded transport."""


def _path_part(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Tesla path identifier cannot be empty")
    return quote(normalized, safe="")
