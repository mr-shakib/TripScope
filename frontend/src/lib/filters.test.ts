import type { Coverage } from "@/api/types";

import {
  activeFilterCount,
  coveragePresets,
  EMPTY_FILTERS,
  monthPresets,
  rangeDays,
  readFilters,
  setDates,
  toggleValue,
  writeFilters,
} from "./filters";

const coverage: Coverage = {
  dataset_id: "nyc-tlc-yellow",
  dataset_name: "Yellow",
  start_date: "2025-01-01",
  end_date: "2025-06-30",
  source_attribution: "TLC",
  periods: [
    { period: "2025-01", row_count: 1, run_id: "a", published_at: "" },
    { period: "2025-02", row_count: 1, run_id: "b", published_at: "" },
  ],
};

describe("filters in the URL", () => {
  it("round-trip every dimension", () => {
    const filters = { start_date: "2025-01-06", end_date: "2025-01-12", pickup_zone: [132, 161], payment_type: [0, 1], hour: [7, 8], weekday: [6, 7] };
    const params = writeFilters(new URLSearchParams("tab=x"), filters);
    expect(params.toString()).toBe("tab=x&start=2025-01-06&end=2025-01-12&pz=132%2C161&pt=0%2C1&h=7%2C8&wd=6%2C7");
    expect(readFilters(params)).toEqual(filters);
  });

  it("drop malformed and out-of-range values instead of sending them", () => {
    const params = new URLSearchParams("start=2025-01-06';DROP&pz=0,132,999,x,132&h=24,23&wd=8&pt=-1,2");
    expect(readFilters(params)).toEqual({ ...EMPTY_FILTERS, pickup_zone: [132], hour: [23], payment_type: [2] });
  });

  it("clear keys when filters reset", () => {
    expect(writeFilters(new URLSearchParams("start=2025-01-06&pz=1"), EMPTY_FILTERS).toString()).toBe("");
  });
});

describe("filter helpers", () => {
  it("toggle values and count active filters", () => {
    let filters = toggleValue(EMPTY_FILTERS, "hour", 8);
    filters = toggleValue(filters, "hour", 7);
    expect(filters.hour).toEqual([7, 8]);
    expect(toggleValue(filters, "hour", 8).hour).toEqual([7]);
    expect(activeFilterCount(setDates(filters, "2025-01-01", "2025-01-31"))).toBe(2);
  });

  it("build presets from the published coverage, not today's date", () => {
    expect(coveragePresets(coverage).map((p) => [p.id, p.start, p.end])).toEqual([
      ["all", undefined, undefined],
      ["last-7", "2025-06-24", "2025-06-30"],
      ["last-30", "2025-06-01", "2025-06-30"],
      ["first-7", "2025-01-01", "2025-01-07"],
    ]);
    expect(monthPresets(coverage).map((p) => [p.label, p.start, p.end])).toEqual([
      ["Jan 2025", "2025-01-01", "2025-01-31"],
      ["Feb 2025", "2025-02-01", "2025-02-28"],
    ]);
  });

  it("compute inclusive range length", () => {
    expect(rangeDays({}, coverage)).toBe(181);
    expect(rangeDays({ start_date: "2025-01-06", end_date: "2025-01-12" }, coverage)).toBe(7);
  });
});
