# Post-freeze comparison

All five solver outputs and their source hashes were frozen before reference unsealing. These analysis scripts do not modify the solver, blind inputs or frozen case directories.

`compare_analysis.py` calls the frozen `compare_reference.compare_boards` for every board. `reference_adapter.py` handles modern name-only net syntax for the installed kiutils parser and verifies equivalent net identities and geometry. `reference_previews.py` uniformly reads both legacy `fp_text` references and footprint properties, then verifies that every reference preview contains the expected component count. The initial parser/display failure records remain in `../v3_validation/`.

To repeat the analysis in the existing workspace (the references are now exposed, so this is no longer a new blind experiment):

```bash
mkdir -p .run
cp artifacts/blind_v3_comparison/compare_analysis.py .run/compare_v3.py
cp artifacts/blind_v3_comparison/reference_adapter.py .run/reference_adapter.py
cp artifacts/blind_v3_comparison/reference_previews.py .run/reference_previews.py
source scripts/env.sh
python .run/compare_v3.py
source scripts/env.sh
python .run/reference_previews.py
```

The JSON retains failed routes, incomplete electrical inputs and unverified mechanical dimensions. Network copper length difference includes branches and is not an endpoint timing proof. Parametric reference models use the same library as solver models, not manufacturer STEP files.
