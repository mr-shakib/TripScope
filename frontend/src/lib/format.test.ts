import { escapeHtml, formatBucket, formatDate, formatDuration, formatPercent, formatValue, MISSING } from "./format";

describe("formatValue", () => {
  it("never renders missing values as zero", () => {
    for (const unit of ["trips", "usd", "miles", "minutes"] as const) {
      expect(formatValue(null, unit)).toBe(MISSING);
      expect(formatValue(Number.NaN, unit)).toBe(MISSING);
    }
    expect(formatValue(0, "trips")).toBe("0");
  });

  it("compacts large values and keeps exact values on request", () => {
    expect(formatValue(3_475_080, "trips")).toBe("3.48M");
    expect(formatValue(3_475_080, "trips", { exact: true })).toBe("3,475,080");
    expect(formatValue(89_465_400.39, "usd")).toBe("$89.47M");
    expect(formatValue(89_465_400.39, "usd", { exact: true })).toBe("$89,465,400.39");
    expect(formatValue(26.861, "usd")).toBe("$26.86");
    expect(formatValue(3.179, "miles")).toBe("3.18 mi");
    expect(formatValue(14.63, "minutes")).toBe("14.6 min");
  });
});

describe("dates", () => {
  it("formats NYC calendar dates without shifting them into the browser time zone", () => {
    expect(formatDate("2025-01-01")).toBe("Jan 1, 2025");
    expect(formatDate("2025-01-31")).toBe("Jan 31, 2025");
    expect(formatBucket("2025-01-06 08:00:00", "hour")).toContain("08:00");
  });
});

describe("escapeHtml", () => {
  it("neutralises markup in labels", () => {
    expect(escapeHtml('<img src=x onerror="alert(1)">')).toBe("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
  });
});

describe("formatPercent", () => {
  it("never shows a partial share as 100% or a non-zero share as 0%", () => {
    expect(formatPercent(24_082_454, 24_083_384)).toBe("99.996%");
    expect(formatPercent(930, 24_083_384)).toBe("<0.01%");
    expect(formatPercent(50, 100)).toBe("50%");
    expect(formatPercent(1, 1)).toBe("100%");
    expect(formatPercent(1, 0)).toBe(MISSING);
  });

  it("formats durations", () => {
    expect(formatDuration(0.25)).toBe("250 ms");
    expect(formatDuration(30.4)).toBe("30.4 s");
    expect(formatDuration(125)).toBe("2m 5s");
  });
});
