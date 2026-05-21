# codar-sounder × Kp correlation analysis

Analysis window: 2026-05-14 00:00 to 2026-05-21 12:00 UTC; source: /var/lib/codar-sounder/ac0g-bee1-rx888/SEAB

Note: 1 bucket(s) at the end of the window lack a published NOAA Kp value (3-hour publication lag); shown in a separate section below.

## Proxy metrics by 3-hour Kp bucket

v0.7+ records carry an ``n_hops`` field; the **multi-hop%** column shows the fraction of records classified as 2+ hop by ``invert()``.  Pre-v0.7 records are counted as 1-hop (legacy default).

| UTC bucket | Kp | level | CPIs | recs | F2_extr% | multi-hop% | mean h' (km) | mean SNR | mean peaks |
|---|---|---|---|---|---|---|---|---|---|
| 2026-05-14 00:00 | 2.67 |     quiet | 708 | 2778 |  35.2 |   0.0 |  450.1 |  36.8 | 3.97 |
| 2026-05-14 03:00 | 1.67 |     quiet | 713 | 2843 |  29.6 |   0.0 |  413.5 |  39.9 | 4.00 |
| 2026-05-14 06:00 | 1.67 |     quiet | 720 | 2880 |  34.2 |   0.0 |  429.2 |  37.6 | 4.00 |
| 2026-05-14 09:00 | 0.67 |     quiet | 719 | 2876 |  33.5 |   0.0 |  428.0 |  37.3 | 4.00 |
| 2026-05-14 12:00 | 1.33 |     quiet | 697 | 2775 |  31.4 |   0.0 |  428.7 |  39.5 | 3.99 |
| 2026-05-14 15:00 | 0.67 |     quiet | 719 | 2876 |  29.5 |   0.0 |  407.4 |  39.6 | 4.00 |
| 2026-05-14 18:00 | 0.67 |     quiet | 719 | 2876 |  33.2 |   0.0 |  428.0 |  37.5 | 4.00 |
| 2026-05-14 21:00 | 1.33 |     quiet | 708 | 2816 |  33.2 |   0.0 |  424.3 |  38.1 | 3.99 |
| 2026-05-15 00:00 | 2.33 |     quiet | 708 | 2793 |  29.1 |   0.0 |  405.8 |  39.6 | 3.98 |
| 2026-05-15 03:00 | 1.67 |     quiet | 717 | 2853 |  35.4 |   0.0 |  445.7 |  36.9 | 3.99 |
| 2026-05-15 06:00 | 3.00 | unsettled | 697 | 2731 |  34.8 |   0.0 |  446.3 |  37.3 | 3.97 |
| 2026-05-15 09:00 | 3.33 | unsettled | 716 | 2856 |  37.6 |   0.0 |  456.9 |  34.8 | 3.99 |
| 2026-05-15 12:00 | 5.00 |        G1 | 719 | 2873 |  30.5 |   0.0 |  419.4 |  39.1 | 4.00 |
| 2026-05-15 15:00 | 5.33 |        G1 | 719 | 2873 |  33.8 |   0.0 |  439.3 |  36.6 | 4.00 |
| 2026-05-15 18:00 | 4.33 |    active | 713 | 2844 |  31.5 |   0.0 |  424.1 |  39.0 | 4.00 |
| 2026-05-15 21:00 | 6.33 |        G2 | 663 | 2598 |  39.5 |   0.0 |  475.6 |  33.5 | 3.97 |
| 2026-05-16 00:00 | 5.67 |        G1 | 701 | 2789 |  27.6 |   0.0 |  400.9 |  41.2 | 3.99 |
| 2026-05-16 03:00 | 5.33 |        G1 | 718 | 2869 |  30.1 |   0.0 |  407.2 |  39.9 | 4.00 |
| 2026-05-16 06:00 | 4.67 |    active | 720 | 2879 |  30.5 |   0.0 |  401.9 |  40.2 | 4.00 |
| 2026-05-16 09:00 | 3.67 | unsettled | 672 | 2688 |  34.2 |   0.0 |  429.2 |  36.9 | 4.00 |
| 2026-05-16 12:00 | 2.33 |     quiet | 421 | 1668 |  36.8 |   0.0 |  450.4 |  36.3 | 3.98 |
| 2026-05-16 15:00 | 4.67 |    active | 720 | 2880 |  32.7 |   0.0 |  418.9 |  37.7 | 4.00 |
| 2026-05-16 18:00 | 3.67 | unsettled | 720 | 2878 |  35.5 |   0.0 |  437.3 |  36.1 | 4.00 |
| 2026-05-16 21:00 | 3.33 | unsettled | 717 | 2864 |  30.6 |   0.0 |  404.2 |  38.2 | 4.00 |
| 2026-05-17 00:00 | 2.00 |     quiet | 719 | 2876 |  27.8 |   0.0 |  390.5 |  40.0 | 4.00 |
| 2026-05-17 03:00 | 2.33 |     quiet | 719 | 2870 |  30.9 |   0.0 |  402.6 |  39.9 | 4.00 |
| 2026-05-17 06:00 | 2.00 |     quiet | 720 | 2880 |  36.0 |   0.0 |  434.0 |  37.1 | 4.00 |
| 2026-05-17 09:00 | 2.33 |     quiet | 720 | 2880 |  34.0 |   0.0 |  428.0 |  37.5 | 4.00 |
| 2026-05-17 12:00 | 2.33 |     quiet | 716 | 2850 |  29.9 |   0.0 |  404.3 |  39.0 | 3.99 |
| 2026-05-17 15:00 | 2.00 |     quiet | 688 | 2742 |  36.8 |   0.0 |  447.3 |  34.9 | 4.00 |
| 2026-05-17 18:00 | 2.33 |     quiet | 675 | 2666 |  41.4 |   0.0 |  475.8 |  31.3 | 3.98 |
| 2026-05-17 21:00 | 2.00 |     quiet | 676 | 2659 |  42.9 |   0.0 |  481.9 |  31.3 | 3.98 |
| 2026-05-18 00:00 | 2.33 |     quiet | 660 | 2565 |  38.5 |   0.0 |  462.6 |  34.2 | 3.96 |
| 2026-05-18 03:00 | 2.33 |     quiet | 716 | 2858 |  30.5 |   0.0 |  416.4 |  38.5 | 4.00 |
| 2026-05-18 06:00 | 2.33 |     quiet | 701 | 2746 |  39.1 |   0.0 |  458.1 |  35.1 | 3.97 |
| 2026-05-18 09:00 | 2.00 |     quiet | 719 | 2874 |  36.6 |   0.0 |  448.7 |  35.4 | 4.00 |
| 2026-05-18 12:00 | 2.67 |     quiet | 683 | 2648 |  45.2 |   0.0 |  506.4 |  32.4 | 3.95 |
| 2026-05-18 15:00 | 2.00 |     quiet | 643 | 2471 |  49.7 |   0.0 |  529.7 |  28.8 | 3.93 |
| 2026-05-18 18:00 | 2.33 |     quiet | 621 | 2397 |  51.3 |   0.0 |  539.4 |  26.6 | 3.94 |
| 2026-05-18 21:00 | 2.67 |     quiet | 655 | 2570 |  45.4 |   0.0 |  497.7 |  32.4 | 3.97 |
| 2026-05-19 00:00 | 1.67 |     quiet | 672 | 2634 |  41.1 |   0.0 |  472.6 |  33.6 | 3.97 |
| 2026-05-19 03:00 | 3.00 | unsettled | 670 | 2622 |  36.7 |   0.0 |  444.5 |  35.9 | 3.96 |
| 2026-05-19 06:00 | 3.67 | unsettled | 655 | 2559 |  43.5 |   0.0 |  489.3 |  32.3 | 3.96 |
| 2026-05-19 09:00 | 4.00 |    active | 671 | 2651 |  39.0 |   0.0 |  461.7 |  34.3 | 3.98 |
| 2026-05-19 12:00 | 4.00 |    active | 673 | 2643 |  42.9 |   0.0 |  488.1 |  30.2 | 3.97 |
| 2026-05-19 15:00 | 4.00 |    active | 1356 | 5299 |  36.7 |   0.0 |  456.8 |  35.5 | 3.96 |
| 2026-05-19 18:00 | 3.00 | unsettled | 760 | 2999 |  36.2 |   0.0 |  453.3 |  35.2 | 3.98 |
| 2026-05-19 21:00 | 2.67 |     quiet | 667 | 2642 |  34.7 |   0.0 |  443.1 |  35.6 | 3.98 |
| 2026-05-20 00:00 | 3.00 | unsettled | 673 | 2635 |  37.4 |   0.0 |  458.4 |  36.2 | 3.97 |
| 2026-05-20 03:00 | 2.67 |     quiet | 683 | 2677 |  42.4 |   0.0 |  485.4 |  31.4 | 3.97 |
| 2026-05-20 06:00 | 2.00 |     quiet | 590 | 2235 |  45.8 |   0.0 |  510.3 |  30.5 | 3.91 |
| 2026-05-20 09:00 | 1.00 |     quiet | 673 | 2623 |  44.0 |   0.0 |  493.5 |  31.1 | 3.96 |
| 2026-05-20 12:00 | 1.67 |     quiet | 664 | 2594 |  36.2 |   0.0 |  455.3 |  35.5 | 3.96 |
| 2026-05-20 15:00 | 0.67 |     quiet | 660 | 2575 |  36.8 |   0.0 |  451.1 |  35.7 | 3.97 |
| 2026-05-20 18:00 | 1.00 |     quiet | 681 | 2669 |  42.9 |   0.0 |  487.1 |  31.1 | 3.97 |
| 2026-05-20 21:00 | 2.33 |     quiet | 655 | 2551 |  53.2 |   0.0 |  545.1 |  24.8 | 3.96 |
| 2026-05-21 00:00 | 3.00 | unsettled | 1216 | 4778 |  40.2 |   0.0 |  479.3 |  32.5 | 3.97 |
| 2026-05-21 03:00 | 2.33 |     quiet | 747 | 2959 |  36.4 |   0.0 |  450.0 |  36.4 | 3.98 |
| 2026-05-21 06:00 | 1.00 |     quiet | 638 | 2463 |  45.1 |   0.0 |  505.2 |  30.0 | 3.95 |
| 2026-05-21 09:00 | 1.33 |     quiet | 822 | 3234 |  42.4 |   0.0 |  485.8 |  31.2 | 3.98 |

## Recent buckets (Kp not yet published)

These buckets cover data captured since the most recent NOAA Kp publication.  Re-run after the next publication cycle (~3 hours) to backfill Kp correlation.

| UTC bucket | Kp | level | CPIs | recs | F2_extr% | multi-hop% | mean h' (km) | mean SNR | mean peaks |
|---|---|---|---|---|---|---|---|---|---|
| 2026-05-21 12:00 | —       |     pending | 355 | 1400 |  32.6 |   3.4 |  432.5 |  38.0 | 3.98 |

## Scintillation metrics by 3-hour Kp bucket (v0.5+ data only)

| UTC bucket | Kp | level | scint recs | event% | σ_φ_str% | mean σ_φ | mean ratio | dechirp_rej/CPI |
|---|---|---|---|---|---|---|---|---|
| 2026-05-21 00:00 | 3.00 | unsettled | 2232 |  99.6 |  85.7 | 1.427 | 1.000 | 0.00 |
| 2026-05-21 03:00 | 2.33 |     quiet | 2777 |  99.1 |  76.9 | 1.260 | 1.000 | 0.00 |
| 2026-05-21 06:00 | 1.00 |     quiet | 2463 |  98.1 |  77.3 | 1.267 | 1.000 | 0.00 |
| 2026-05-21 09:00 | 1.33 |     quiet | 3234 |  99.2 |  85.3 | 1.347 | 1.075 | 0.00 |
| 2026-05-21 12:00 | —       |     pending | 1400 |  74.9 |  45.1 | 1.158 | 1.303 | 1.36 |

## Aggregated by Kp severity

| Kp level | n buckets | total CPIs | F2_extr% | mean h' (km) | scint recs | event% | strong σ_φ% |
|---|---|---|---|---|---|---|---|
|     quiet | 39 | 26732 |  37.6 |  455.2 | 8474 |  98.9 |  80.2 |
| unsettled | 10 | 7496 |  36.8 |  451.5 | 2232 |  99.6 |  85.7 |
|    active | 6 | 4853 |  35.6 |  443.0 | 0 |   0.0 |   0.0 |
|        G1 | 4 | 2857 |  30.5 |  416.8 | 0 |   0.0 |   0.0 |
|        G2 | 1 | 663 |  39.5 |  475.6 | 0 |   0.0 |   0.0 |

## Re-run findings (2026-05-21 ~13:30 UTC)

This re-run was generated after deploying v0.6.2 / v0.6.3 / v0.7.0
earlier today.  NOAA Kp publication lags the data by 3+ hours, so
the 12:00 UTC bucket has no Kp value yet — captured in the "Recent
buckets" section above.

The 12:00 bucket is a Frankenstein average of all today's
deployments because the service restarted across each release:

  - 11:30 UTC: v0.5.1 deployed (MAD outlier rejection)
  - 11:45 UTC: v0.5.2 deployed (σ_φ HF #1)
  - 11:55 UTC: v0.6.0 deployed (σ_φ diagnostics)
  - 12:00 UTC: v0.6.1 deployed (sweep MAD pre-filter)
  - 12:13 UTC: σ_φ MAD coordination fix folded into v0.6.1
  - 12:48 UTC: v0.6.2 deployed (σ_φ Kp-calibrated thresholds)
  - 13:13 UTC: v0.6.3 deployed (S4 Kp-calibrated thresholds)
  - 13:24 UTC: v0.7.0 deployed (multi-hop inversion)

Visible effects in the 12:00 bucket relative to the pre-noon
buckets:

  - **event rate**: 98-99% → 74.9% (σ_φ + S4 thresholds raised)
  - **σ_φ strong%**: 77-86% → 45.1%
  - **mean σ_φ**: 1.27-1.43 rad → 1.158 (calibration; possibly
    also gentler ionosphere)
  - **mean underfit_ratio**: 1.00-1.07 → 1.30 (out of the
    saturated σ_φ regime where both estimators agreed; now
    visibly different)
  - **dechirp_rej/CPI**: 0.00 → 1.36 (v0.6.1 sweep MAD active)
  - **multi-hop%**: n/a → 3.4% (only ~14 min of v0.7.0 in a
    180-min bucket; pure-v0.7 portion is much higher)
  - **F2_extreme%**: 32.6% (down from 35-53% earlier today;
    blended; pure-v0.7 portion is ~0%)

The pre-noon buckets (with published Kp) remain identical to the
original report's findings — the historical record is frozen.
Re-running tomorrow once NOAA publishes 12:00 + 15:00 + ... Kp
will give the first clean look at the v0.7.0 calibration against
Kp.
