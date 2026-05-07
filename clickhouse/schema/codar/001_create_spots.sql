-- codar-sounder: codar.spots — one row per ionospheric peak per CPI.
--
-- Each CPI's range profile may yield multiple peaks (high-ray /
-- low-ray F2, plus an E-layer return, etc.); the daemon emits one row
-- per peak with `peak_index` ranking them by SNR descending and
-- `peak_count` carrying the per-CPI total so a downstream consumer
-- can regroup peaks back into their parent CPI.
--
-- `mode_layer` is a coarse virtual-height classifier
-- (`E`/`F1`/`F2`/`F2_extreme`/`below_E`/`unknown`) — see
-- codar_sounder/core/invert.py classify_layer().
--
-- HDF5 files written by core/output.py JsonlWriter remain the canonical
-- per-station L1 artefact (Kaeppler-compatible Zenodo schema); this
-- table is the L2 / "shippable" aggregate sink that hs-uploader will
-- read from and ship upstream to PSWS.

CREATE TABLE IF NOT EXISTS codar.spots
(
    -- common header
    time               DateTime64(3, 'UTC')   CODEC(Delta(8), ZSTD(1)),
    host_call          LowCardinality(String) CODEC(LZ4),
    host_grid          LowCardinality(String) CODEC(LZ4),
    radiod_id          LowCardinality(String) CODEC(LZ4),
    instance           LowCardinality(String) CODEC(LZ4),
    processing_version LowCardinality(String) CODEC(LZ4),

    -- transmitter identity + measurement context
    station_id            LowCardinality(String) CODEC(LZ4),     -- e.g. "LISL", "ASSA"
    oblique_freq_hz       Int64                  CODEC(Delta(8), ZSTD(3)),
    sweep_rate_hz_per_s   Float64                CODEC(Delta(8), ZSTD(3)),
    coherent_seconds      Float32                CODEC(ZSTD(3)),

    -- per-peak ranking within the CPI
    peak_index            UInt8                  CODEC(T64, ZSTD(1)),  -- 0 = strongest
    peak_count            UInt8                  CODEC(T64, ZSTD(1)),  -- peaks reported this CPI
    mode_layer            LowCardinality(String) CODEC(LZ4),           -- E/F1/F2/...

    -- detection
    snr_db                Float32                CODEC(Delta(4), ZSTD(3)),

    -- raw geometric measurement
    group_range_km        Float32                CODEC(Delta(4), ZSTD(3)),
    ground_distance_km    Float32                CODEC(Delta(4), ZSTD(3)),

    -- inverted ionospheric quantities (Kaeppler Eq. 10/11/13/14)
    virtual_height_km                          Float32 CODEC(Delta(4), ZSTD(3)),
    virtual_height_uncertainty_km              Float32 CODEC(Delta(4), ZSTD(3)),
    equivalent_vertical_freq_mhz               Float32 CODEC(Delta(4), ZSTD(3)),
    equivalent_vertical_freq_uncertainty_mhz   Float32 CODEC(Delta(4), ZSTD(3)),
    takeoff_zenith_deg                         Float32 CODEC(Delta(4), ZSTD(3)),

    ingested_at        DateTime DEFAULT now() CODEC(Delta(4), ZSTD(1))
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(time)
ORDER BY (host_call, station_id, time, peak_index)
SETTINGS index_granularity = 32768;
