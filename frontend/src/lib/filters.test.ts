import type { Coverage } from "../api/types";
import { coveragePresets, rangeDays, readDateFilters, writeDateFilters } from "./filters";

const coverage: Coverage = {
  dataset_id: "nyc-tlc-yellow",
  dataset_name: "Yellow",
  start_date: "2025-01-01",
  end_date: "2025-01-31",
  source_attribution: "TLC",
  periods: [],
};

describe("date filters in the URL", () => {
  it("round-trips valid dates and drops malformed ones", () => {
    const params = writeDateFilters(new URLSearchParams("tab=1"), { start_date: "2025-01-06", end_date: "2025-01-12" });
    expect(params.toString()).toBe("tab=1&start=2025-01-06&end=2025-01-12");
    expect(readDateFilters(params)).toEqual({ start_date: "2025-01-06", end_date: "2025-01-12" });
    expect(readDateFilters(new URLSearchParams("start=2025-01-06';DROP&end=yesterday"))).toEqual({});
  });

  it("clears a date when reset", () => {
    expect(writeDateFilters(new URLSearchParams("start=2025-01-06"), {}).toString()).toBe("");
  });
});

describe("coverage presets", () => {
  it("are relative to the published data, not today's date", () => {
    const presets = coveragePresets(coverage);
    expect(presets.map((p) => p.filters)).toEqual([
      {},
      { start_date: "2025-01-01", end_date: "2025-01-07" },
      { start_date: "2025-01-25", end_date: "2025-01-31" },
    ]);
    expect(coveragePresets(undefined)).toEqual([]);
  });

  it("computes inclusive range length", () => {
    expect(rangeDays({}, coverage)).toBe(31);
    expect(rangeDays({ start_date: "2025-01-06", end_date: "2025-01-12" }, coverage)).toBe(7);
  });
});
