"""Pure native HID coordinate mapping; no I/O or implicit clamping."""
from __future__ import annotations
import math

WIDTH, HEIGHT = 496, 1080
SUPPORTED_DESCRIPTOR_VERSIONS = (1, 3)  # v2 experiment failed iPhone motion validation


def validate_profile(profile, *, device_id, phone_id, descriptor_version, geometry, require_verified=True):
    if type(descriptor_version) is not int or descriptor_version not in SUPPORTED_DESCRIPTOR_VERSIONS:
        raise ValueError('Unsupported pointer descriptor')
    if not isinstance(profile, dict) or profile.get('schema_version') != 1:
        raise ValueError('Invalid profile schema')
    if not device_id or not phone_id or profile.get('device_id') != device_id or profile.get('phone_id') != phone_id:
        raise ValueError('Profile identity mismatch')
    if profile.get('descriptor_version') != descriptor_version or geometry != (WIDTH, HEIGHT, 'portrait'):
        raise ValueError('Profile geometry or descriptor mismatch')
    if profile.get('geometry') != [WIDTH, HEIGHT, 'portrait'] or profile.get('coordinate_space') != 'full_screenshot_pixels':
        raise ValueError('Profile geometry mismatch')
    if require_verified and profile.get('verification') != 'verified':
        raise ValueError('Profile requires verification')
    if not isinstance(profile.get('profile_id'), str) or not profile['profile_id']:
        raise ValueError('Missing profile ID')
    region = profile.get('verified_region')
    if not isinstance(region, list) or len(region) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in region):
        raise ValueError('Invalid verified region')
    x0, y0, x1, y1 = region
    if not (0 <= x0 < x1 < WIDTH and 0 <= y0 < y1 < HEIGHT):
        raise ValueError('Invalid verified region')
    _validate_axes(profile.get('axes'))
    return profile


def _validate_axes(axes):
    """All callers share fixed positive axes, including direct map_target users."""
    if not isinstance(axes, list) or len(axes) != 2:
        raise ValueError('Invalid axes')
    for axis in axes:
        if not isinstance(axis, dict): raise ValueError('Invalid axis')
        slope, offset = axis.get('slope'), axis.get('offset')
        if type(slope) not in (int, float) or type(offset) not in (int, float) or not math.isfinite(slope) or not math.isfinite(offset) or not .8 < slope < 1.2 or abs(offset) > 50:
            raise ValueError('Invalid mapping: fixed portrait requires positive direction')


def map_target(x, y, profile=None, *, click=False):
    if type(x) is not int or type(y) is not int or not (0 <= x < WIDTH and 0 <= y < HEIGHT):
        raise ValueError('Target outside screenshot')
    if click and profile is None:
        raise ValueError('Verified profile required for click')
    coords = [float(x), float(y)]
    if profile is not None:
        _validate_axes(profile.get('axes'))
        # verified_region records measured coverage, not a click boundary.
        coords = [(coords[i]-profile['axes'][i]['offset'])/profile['axes'][i]['slope'] for i in (0, 1)]
    if not (0 <= coords[0] <= WIDTH-1 and 0 <= coords[1] <= HEIGHT-1):
        raise ValueError('Corrected target outside screenshot')
    return [round(coords[0]/(WIDTH-1)*32767), round(coords[1]/(HEIGHT-1)*32767)]
