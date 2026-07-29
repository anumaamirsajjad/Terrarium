"""The thermal core - v1's only physics core.

A LightGBM emulator predicting land surface temperature from land cover, albedo,
NDVI, imperviousness, and meteorology. Trained against observed Landsat LST so it
learns the local empirical relationship rather than assuming a hand-rolled surface
energy balance.
"""
