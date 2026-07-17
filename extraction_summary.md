# Manual Extraction Summary

This extraction used text-readable content from:

1. Specs_Manuals/02_1finity/FUJITSU 1FINITY L100 USER MANUAL.pdf
2. Specs_Manuals/02_1finity/1FINITY_光入出力規格_Ver1.2.pdf

## Outputs

1. dco_spec.json
2. roadm_spec.json

## Traceability

| Output field | Extracted value | Source reference | Notes |
|---|---:|---|---|
| modules[0].name | FUJITSU-1FINITY-T700-EX | 1FINITY_光入出力規格_Ver1.2.pdf (T700/PIT7-P7A1 section) | Module naming normalized for JSON use |
| supported_profiles[0].speed_gbps | 200 | 1FINITY_光入出力規格_Ver1.2.pdf (T700 Ex: 200G lines) | Directly stated |
| supported_profiles[0].modulation | 16QAM | 1FINITY_光入出力規格_Ver1.2.pdf (line: 200G/λ 16QAM) | Directly stated |
| supported_profiles[0].max_distance_km | 770.0 | FUJITSU 1FINITY L100 USER MANUAL.pdf, Table 2 ("50 GHz, 16QAM, Maximum reach: 770 km", OSNR OFF) | Conservative value selected from OSNR OFF mode |
| supported_profiles[0].launch_power_dbm | -5.0 | 1FINITY_光入出力規格_Ver1.2.pdf ("送信レベル -5.0～0.0dBm") | Conservative lower bound |
| supported_profiles[0].max_path_loss_db | 13.0 | 1FINITY_光入出力規格_Ver1.2.pdf (Tx -5.0 to 0.0 dBm, Rx -18.0 to +1.0 dBm for 200G/λ 16QAM) | Derived as conservative optical budget: -5 - (-18) = 13 dB |
| roadm_spec.vendor | Fujitsu | Document publisher/headers | Direct |
| roadm_spec.model | 1FINITY L100/L200 ROADM | Manual titles | Direct model family reference |
| roadm_spec.node_loss_db | 0.0 | Not found explicitly | Temporary simulator default pending vendor-confirmed node/transit loss |

## Missing or uncertain fields

1. Explicit ROADM transit node loss/insertion loss value for node_loss_db.
2. Additional DCO profiles for speed/modulation combinations (for example 100G QPSK, 400G 16QAM) with both max_path_loss_db and max_distance_km.
3. Explicit OSNR thresholds and Q-value thresholds per profile.

## Recommended follow-up

1. Confirm ROADM express/transit node insertion loss from detailed engineering tables and update roadm_spec.json node_loss_db.
2. Extract profile limits from DCO-specific transponder datasheet pages for each target service profile used in wavelength_plan.json.
3. If you share exact page references or screenshots for the missing values, the JSON can be finalized without assumptions.
