# Spec Extraction Agent Instructions

Use this instruction set when asking an agent to read DCO and ROADM datasheets/manuals and produce simulator-ready JSON files.

## Goal

Produce two files:

1. dco_spec.json
2. roadm_spec.json

The output must match the structures shown in [dco_spec.template.json](dco_spec.template.json) and [roadm_spec.template.json](roadm_spec.template.json).

## Extraction rules

1. Only extract values explicitly present in the documents.
2. If multiple candidate values exist, choose the one matching coherent line-side operation and record the reason in a notes field.
3. If a required field is missing, put null and list it in a "missing_fields" section in a companion markdown summary.
4. Normalize units:
   - dB fields as float in dB.
   - Distance as float in km.
   - Speed as integer in Gbps.
5. Normalize modulation strings to uppercase (for example: QPSK, 8QAM, 16QAM).

## Required dco_spec fields

1. modules[].name
2. modules[].supported_profiles[].speed_gbps
3. modules[].supported_profiles[].modulation
4. modules[].supported_profiles[].max_path_loss_db
5. modules[].supported_profiles[].max_distance_km

## Optional but recommended dco_spec fields

1. q_offset_db (global or per profile)
2. launch_power_dbm
3. initial_osnr_db
4. min_osnr_db
5. min_q_value

## Required roadm_spec fields

1. vendor
2. model
3. node_loss_db

## Output quality checks

1. Confirm every dco profile has both max_path_loss_db and max_distance_km.
2. Confirm node_loss_db is numeric.
3. Confirm no string numbers remain for numeric fields.
4. Provide a traceability table in markdown:
   - output field
   - extracted value
   - source document and section/page reference

## Suggested agent prompt

Read the attached DCO and ROADM manuals and extract simulator inputs.

Deliverables:

1. dco_spec.json (matching dco_spec.template.json)
2. roadm_spec.json (matching roadm_spec.template.json)
3. extraction_summary.md with:
   - assumptions
   - missing fields
   - field-to-source traceability table

Constraints:

1. Do not invent values.
2. Use null for missing required values and list them explicitly.
3. Keep numeric values as numbers, not strings.
