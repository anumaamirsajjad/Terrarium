"""The thermal core - v1's only physics core.

A LightGBM emulator predicting **mid-morning land surface temperature** from land cover,
albedo, NDVI and urban form. Trained against observed Landsat ST_B10 so it learns the
local empirical relationship rather than assuming a hand-rolled surface energy balance.

The target is what it says: *surface* temperature, at the ~10:30 local Landsat overpass.
It runs several degrees above air temperature and peaks after the overpass, so never
describe an output as "temperature" unqualified, and never as an afternoon figure (D9).

Meteorology is absent by design in this phase - a single-date composite makes wind and
air temperature constant across all 40,602 pixels, carrying exactly zero information.
They arrive with the time dimension in Phase 4.
"""
