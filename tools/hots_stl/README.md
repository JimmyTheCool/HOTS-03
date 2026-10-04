# HOTS M3-to-STL builder

This branch contains an automated, reproducible conversion pipeline for the Heroes of the Storm `.m3` character meshes stored in `HOTS-02` and `HOTS-03`.

The GitHub Actions workflow pins Blender 3.6.23 and the M3 importer commit, discovers one canonical base mesh per playable hero, applies a neutral animation pose where available, and emits:

- a detail-preserving STL;
- a based, minimum-thickness, voxel-remeshed, manifold-oriented STL;
- a JSON/CSV manifest with source paths, hashes, dimensions, triangle counts and validation results;
- a downloadable ZIP published as a GitHub prerelease.

Use the workflow's `smoke` mode first. It converts Abathur, Johanna and Xal'atath, covering assets from both source repositories and different release eras. The `full` mode processes the 91-entry playable roster.

STL cannot retain textures, material shaders, transparency, emissive effects or normal-map detail. The generated geometry is intended for personal, non-commercial fan use only.
